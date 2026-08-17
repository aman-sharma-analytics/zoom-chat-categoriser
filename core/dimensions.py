# -*- coding: utf-8 -*-
"""Phrase-bank dimension scorer -- extracted from the be10x engine's run.py
(compile_cfg + the flush()/score() message loop) and auto_tune.py (paste guard).

Produces exactly what categorize() consumes: D1/D3/D4/D5/D7 plus the three message
counts (Intent / Meaningful / Neg msgs). D2 (engagement volume), D6 (cross-week
persistence) and D8 (sentiment) are deliberately not computed -- categorize() never
reads them and D6 needs multi-week state this tool does not keep.

Hard-won rules preserved:
  * An EMPTY phrase bank compiles to re.compile('') which matches EVERY string --
    refused at load with a named error, never compiled.
  * Pasted third-party content (forum reviews / FUD) gets no intent credit.
  * 'pos' hits on a message that also matches negativity are not counted as positive.
"""
import json
import re

NUM_RE = re.compile(r'^[\d\W]+$')
WORD_RE = re.compile(r"[a-z']+|[^\W\da-z_]+")   # Devanagari/CJK tokens count toward 'meaningful'

# ---------------------------------------------------------------- paste guard (auto_tune.py)
# A forum/review timestamp ("10d ago") almost never appears in genuine live chat.
_STRONG_PASTE = re.compile(
    r"(?:•\s*)?\b\d+\s*(?:mo|min|mins|hr|hrs|sec|secs|d|h|w|y)\s+ago\b", re.I)
# Weaker markers: need two or more before a line is treated as pasted.
_WEAK_PASTE = [re.compile(p, re.I) for p in [
    r"\b(?:u/|r/)[A-Za-z0-9_]{3,}\b",                       # reddit handles / subs
    r"\b\d+\s+(?:upvotes?|downvotes?|likes?|repl(?:y|ies)|comments?|shares?)\b",
    r"\b(?:upvote|downvote|reblog|retweet|original poster)\b",
    r"\b(?:originally posted|posted by|read more|show more|see more)\b",
    r"★|⭐|\b[0-5](?:\.\d)?\s*/\s*5\b",            # star ratings
    r"\b\d+\s*(?:day|days|hour|hours|week|weeks|month|months|year|years)\s+ago\b",
]] + [
    # CamelCase-username pattern: CASE-SENSITIVE, two humps (MarionberryAnnual908, not Office365)
    re.compile(r"\b[A-Z][a-z]+[A-Z][A-Za-z]*\d{2,}\b"),
]


def is_pasted(msg):
    """True if a chat line looks like pasted third-party forum/review content."""
    if not msg:
        return False
    if _STRONG_PASTE.search(msg):
        return True
    return sum(1 for w in _WEAK_PASTE if w.search(msg)) >= 2


# ---------------------------------------------------------------- config
def load_config(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def compile_cfg(cfg):
    """'|'.join([]) is '' and re.compile('') MATCHES EVERY STRING. The JSON is the
    user-editable knob, so an empty bank is always an editing mistake -- refuse it by
    name rather than silently firing a category for every attendee."""
    for _c, _pl in list(cfg["phrase_banks"].items()) + [("negativity_patterns", cfg["negativity_patterns"])]:
        if not _pl or any((not str(p).strip()) for p in _pl):
            raise ValueError("phrase_banks.json: phrase bank %r is empty or contains a blank pattern -- "
                             "an empty bank compiles to a regex that matches EVERY message. Remove the key "
                             "entirely, or give it at least one real pattern." % _c)
    PAT = {c: re.compile('|'.join(pl)) for c, pl in cfg["phrase_banks"].items()}
    NEG = re.compile('|'.join(cfg["negativity_patterns"]))
    ANY = re.compile('|'.join('(?:' + p.pattern + ')' for p in list(PAT.values()) + [NEG]))
    NOISE = set(cfg["noise_exact"])
    return PAT, NEG, ANY, NOISE


def is_noise(m, NOISE):
    m = m.strip()
    return (m in NOISE) or len(m) <= 2 or (bool(NUM_RE.match(m)) and not re.search(r'\d{3,}', m))


class Scorer:
    """Compiled banks + weights; score() takes one person's messages."""

    def __init__(self, cfg):
        self.PAT, self.NEG, self.ANY, self.NOISE = compile_cfg(cfg)
        self.STRONG = list(self.PAT.keys())
        self.BUY = set(cfg["buying_categories"])
        D = cfg["dimensions"]
        self.weights = {
            'D1': (D["D1_buying_language"]["weights"], D["D1_buying_language"]["max"]),
            'D3': (D["D3_urgency"]["weights"], D["D3_urgency"]["max"]),
            'D4': (D["D4_fit_usecase"]["weights"], D["D4_fit_usecase"]["max"]),
            'D5': (D["D5_objection_quality"]["weights"], D["D5_objection_quality"]["max"]),
            'D7': (D["D7_decision_readiness"]["weights"], D["D7_decision_readiness"]["max"]),
        }

    def score_messages(self, messages):
        """messages: one person's chat messages (each = one header block's body joined
        with ' ', the engine's flush() unit), posting order.

        Returns {'D1','D3','D4','D5','D7', 'mean','intent','neg','pos',
                 'cat': {bank: msg hits}, 'pasted', 'pasted_blocked'}."""
        cat = {}
        mean = intent = neg = pos = pasted_ct = pasted_blocked = 0
        for msg in messages:
            msg = str(msg).strip()
            if not msg:
                continue
            ml = msg.lower()
            noisy = is_noise(ml, self.NOISE)
            if (not noisy) and len(WORD_RE.findall(ml)) >= 2:
                mean += 1
            isneg = bool(self.NEG.search(ml))
            if isneg:
                neg += 1
            pasted = is_pasted(msg)
            if pasted:
                pasted_ct += 1
            hitbuy = False
            if (not noisy) and self.ANY.search(ml):
                if pasted:
                    # Third-party pasted content: do NOT credit the person's own
                    # buying/objection intent; just note it was blocked.
                    for c in self.STRONG:
                        if c != 'pos' and c in self.BUY and self.PAT[c].search(ml):
                            pasted_blocked += 1
                            break
                else:
                    for c in self.STRONG:
                        if self.PAT[c].search(ml):
                            if c == 'pos' and isneg:
                                continue
                            cat[c] = cat.get(c, 0) + 1
                            if c in self.BUY:
                                hitbuy = True
                            if c == 'pos':
                                pos += 1
            if hitbuy:
                intent += 1
        h = lambda k: cat.get(k, 0) > 0
        out = {}
        for d, (w, mx) in self.weights.items():
            out[d] = min(mx, sum(wt for c, wt in w.items() if h(c)))
        out.update(mean=mean, intent=intent, neg=neg, pos=pos, cat=cat,
                   pasted=pasted_ct, pasted_blocked=pasted_blocked)
        return out
