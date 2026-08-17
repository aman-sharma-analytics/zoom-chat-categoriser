# -*- coding: utf-8 -*-
"""
chat_cleaner.py -- Workshop chat cleaning, v1.2
Implements _lead_scoring_engine/chat_cleaning_algorithm.md (v1.1) plus the
v1.2 anti-anomaly amendments approved by the user on 2026-08-03:
  * STRICT SPLIT EVERYWHERE -- the section-6 low-volume rescue no longer moves
    ignorable messages into Relevant (user decision; junk-only small chatters
    now show a blank Relevant Chat and a populated Deleted Chat).
  * Expanded host-token vocabulary (blueprint, bonus, action, brand-poll
    single words, bare "N day(s)" ...).
  * Repetition collapse: per-token glued repeats (MEMEMEME -> ME), whole-message
    token-period repeats (Boom Boom -> Boom; phrase x3 -> phrase), and raw
    glued whole-message repeats ("app?free ...app?" -> once).
  * Character hardening: emoji ranges extended (U+2300-23FF watch/clocks,
    keycap U+20E3, VS15 U+FE0E), format/control chars stripped, combining
    accents dropped when attached to ASCII (10x̌x -> 10xx). Indic scripts
    are untouched.
  * audit_relevant() -- the recurring anomaly-detection pass; run it on every
    build and surface its findings in the run report.
Pipeline: split -> per-message clean -> drop-empties -> dedup (hardened key)
       -> near-dup collapse -> classify -> join.   Pure stdlib. Deterministic.
"""
import re
from difflib import SequenceMatcher

SEP = " | "
VERSION = "1.3"

# ---------------- Stage 2 -- per-message cleaning ----------------
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿️‍\U0001F1E6-\U0001F1FF"
    "\U0001F3FB-\U0001F3FF⬀-⯿←-⇿✀-➿⌀-⏿⃣︎]"
)
# [v1.3 2026-08-05, bug review M22] _FMTCTL stripped a NARROWER set than audit_relevant flags
# (which is anything in Unicode category Cf/Cc/Co/Cs). The gap included bidi embedding/override
# U+202A-202E, invisible operators U+2061-2064, and the TAG characters U+E0020-E007F used in
# emoji tag sequences -- so an ordinary flag emoji (England/Scotland/Wales) left its tag payload
# behind after _EMOJI removed the base, and the audit then reported bad_chars > 0. That was a
# cosmetic warning before; M12 made the audit a BLOCKING gate, so an attendee typing a flag
# could stop the weekly build. Now covers every Cf/Cc/Co/Cs range. \x09 and \x0A stay out of the
# class because _WS has already normalised all whitespace to single spaces before this runs.
_FMTCTL = re.compile(
    "[\x00-\x08\x0B-\x1F\x7F-\x9F"                 # Cc
    "\xad؀-؅؜۝܏࣢᠎"   # Cf
    "​-‏‪-‮⁠-⁤⁦-⁯"
    "﻿￹-￻"
    "\ud800-\udfff"                                # Cs (surrogates)
    "-"                                # Co (private use, BMP)
    "\U000110bd\U000110cd\U00013430-\U00013438"    # Cf (supplementary)
    "\U0001bca0-\U0001bca3\U0001d173-\U0001d17a"
    "\U000e0001\U000e0020-\U000e007f"              # Cf language tags / emoji tag sequences
    "\U000f0000-\U000ffffd\U00100000-\U0010fffd"   # Co (private use, supplementary)
    "]"
)
_COMB_ASCII = re.compile(r"([\x20-\x7E])[̀-ͯ]+")
_WS = re.compile(r"\s+")
_LEADP = re.compile(r"^[^\w]+")                    # leading punctuation
_LRUN = re.compile(r"(?i)([a-z])\1{2,}")           # letter runs 3+ -> 1
_QRUN = re.compile(r"\?{2,}")
_XRUN = re.compile(r"!{2,}")
_WRUN = re.compile(r"(?i)\b(\S+)(?:\s+\1\b){2,}")  # word runs 3+ -> 1
_EMOTICONS = {":)", ":(", ":D", ":P", ":p", ";)", ":'(", ":O", ":o", "<3",
              ":-)", ":-(", ";-)", ":-D", ":-P", "XD", "xD", ":/", ":|"}
# [v1.3 2026-08-05, bug review C3] ANY Unicode letter or digit, underscore excluded.
# This was r"[0-9A-Za-z-￿]" -- the  that should have opened the final range was
# MISSING from the source, so the '-' parsed as a LITERAL and the class matched ASCII only.
# Every message written purely in a non-Latin script therefore failed the punctuation-only
# guard below, returned '', and was dropped at Stage 3 -- landing in NEITHER Relevant NOR
# Deleted. 29 of 39 Indic/Arabic messages in the 2 Aug file were destroyed this way, including
# a refund demand. [^\W_] is used instead of an explicit range because it is Unicode-aware and,
# unlike -￿, does not silently promote currency signs and stray symbols to "letters".
_ALNUM = re.compile(r"[^\W_]")

# [v1.3 2026-08-05, bug review H11] Real words that happen to be a repeated syllable.
# _period_collapse is applied to EVERY token with no vocabulary check, so any 4+ character
# doubled-syllable word was truncated to its first period: the spec's own example 'Tata Motors'
# shipped as 'Ta Motors' (141 live instances in the 2 Aug file), 'Papa is retired' as 'Pa is
# retired', and 'murmur' -> 'mur' additionally flipped the message into Deleted. Kinship terms
# and brand names carry real meaning in this chat corpus, so they are protected by name.
# Laughs (haha/hehe) are deliberately NOT here -- collapsing those is correct.
_REAL_DOUBLED = {
    # kinship / address (very common in this corpus)
    "papa", "mama", "baba", "dada", "nana", "didi", "chacha", "tata", "kaka", "bhai bhai",
    # brands / proper nouns
    "coco", "tomtom", "bonbon", "pompom",
    # ordinary English words that are periodic
    "murmur", "tartar", "dodo", "yoyo", "cancan", "couscous", "beriberi", "tsetse",
    "bulbul", "tuktuk", "chowchow", "dumdum", "cuscus", "mishmish",
}


def _period_collapse(s, min_len=4):
    """[v1.2] If s is a string t repeated k>=2 times (case-insensitive,
    len>=min_len, not a pure number), return the first period of the ORIGINAL
    string. 'MEMEMEME' -> 'ME'; 'skipSkip' -> 'skip'; '2020' stays.
    [v1.3] Real periodic words (_REAL_DOUBLED) are exempt -- see bug review H11."""
    n = len(s)
    if n < min_len or s.isdigit():
        return s
    cf = s.casefold()
    if len(cf) != n:                                # rare casefold expansion
        return s
    if cf in _REAL_DOUBLED:                         # [v1.3] 'Tata' is not a repeat of 'Ta'
        return s
    for p in range(1, n // 2 + 1):
        if n % p == 0 and cf[:p] * (n // p) == cf:
            return s[:p]
    return s


def _ws_glued_collapse(m):
    """[v1.2] Whitespace-insensitive whole-message repeat: 'MB MBMB' -> 'MB',
    '10X 10 X 10X' -> '10X'. Rebuilds from the original by consuming the
    first period's worth of non-space characters."""
    stripped = "".join(m.split())
    t = _period_collapse(stripped)
    if t == stripped:
        return m
    want = len(t)
    out = []
    for ch in m:
        if not ch.isspace():
            want -= 1
        out.append(ch)
        if want == 0:
            break
    return "".join(out).strip()


def _collapse_message_repeats(m):
    """[v1.2] Stage 2.7b/c -- repetition collapse beyond single-word runs.
    Iterated with the word-run rule to a joint fixpoint by the caller."""
    toks = [_period_collapse(t) for t in m.split()]  # glued per-token repeats
    n = len(toks)
    if n >= 2:                                      # whole-message token period
        cf = [t.casefold() for t in toks]
        for p in range(1, n // 2 + 1):
            if n % p == 0 and cf[:p] * (n // p) == cf:
                toks = toks[:p]
                break
    m = " ".join(toks)
    return _ws_glued_collapse(m)                    # glued whole-message repeat


def clean_message(raw):
    """Stages 2.1-2.8. Returns cleaned text ('' => drop at Stage 3)."""
    m = _WS.sub(" ", raw).strip()
    if m == "?":                                   # standalone '?' survives (spec 4)
        return "?"
    if m in _EMOTICONS:                            # whole-message text emoticon
        return ""
    m = _EMOJI.sub("", m)                          # 2.1 strip emoji
    m = _FMTCTL.sub("", m)                         # 2.1b [v1.2] format/control chars
    m = _COMB_ASCII.sub(r"\1", m)                  # 2.1c [v1.2] accents on ASCII
    m = _WS.sub(" ", m).strip()                    # 2.2 collapse whitespace
    if not _ALNUM.search(m):                       # punctuation-only ('??', ':)')
        return ""
    m = _LEADP.sub("", m)                          # 2.3 strip LEADING punct only
    m = _LRUN.sub(r"\1", m)                        # 2.4 letter runs 3+ -> 1
    m = _QRUN.sub("?", m)                          # 2.6 '?' runs -> single
    m = _XRUN.sub("!", m)                          # 2.6 '!' runs -> single
    prev = None                                    # 2.7 + 2.7b/c [v1.2]:
    while prev != m:                               # word runs, token/glued
        prev = m                                   # periods -- joint fixpoint
        m = _WRUN.sub(r"\1", m)
        m = _collapse_message_repeats(m)
    m = _WS.sub(" ", m).strip()
    return m if _ALNUM.search(m) else ""


# ---------------- Stage 4 -- dedup keys ----------------
_EDGE = re.compile(r"^[\W_]+|[\W_]+$")


def hardened_key(cleaned):
    """Strip edge non-alphanumerics, collapse ws, casefold."""
    return _WS.sub(" ", _EDGE.sub("", cleaned)).strip().casefold()


# ---------------- Stage 5 -- classification ----------------
_LANGS = {"hindi", "marathi", "telugu", "tamil", "kannada", "bengali", "gujarati",
          "gujrati", "malayalam", "punjabi", "odia", "urdu", "hinglish"}
_VOCAB_IGN = set(_LANGS) | {
    # engagement tokens (host call-and-response)
    "mb", "amb", "tmb", "bmb", "mbb", "mmb", "10x", "100x", "1000x", "10xx",
    "10 x", "swp", "gd", "gc", "pe", "sh", "bp", "nb", "mv", "ywp", "magic",
    "boom", "bom", "ready", "readyy", "readdy", "sup", "wow", "woww",
    "excited", "exited", "tata", "jd", "ss",
    # [v1.2] host-token additions (user-approved 2026-08-03)
    "blueprint", "blue print", "bonus", "action", "agree", "discipline",
    "consistency", "wanted", "fine", "absolutely", "more", "lets go",
    "let's go", "letsgo", "lessgo", "lesgo",
    # affirmations / acks
    "yes", "yess", "yees", "ys", "ye", "yeah", "yea", "yep", "yup", "ya",
    "yes sir", "yes me", "yes yes", "yes.", "no", "noo", "nope", "ok", "okay",
    "ok sir", "sure", "done", "clear", "right", "correct", "true", "i am",
    "i do", "we", "me", "mee", "one", "please", "am", "it", "sir", "hi",
    "hello", "good morning", "good deal",
    # one-word praise / thanks
    "amazing", "awesome", "superb", "super", "excellent", "great", "good",
    "nice", "very good", "very nice", "mind blown", "mindblown", "thank you",
    "thanks", "crazy",
    # poll one-worders (brands/stocks answered to on-stage polls) [v1.2 adds]
    "hdfc", "tcs", "itc", "mrf", "gold", "be10x", "apple", "amazon",
    "reliance", "wipro", "samsung", "tesla", "google", "microsoft", "infosys",
    "adani", "ambani", "zomato", "swiggy", "jio", "airtel", "sbi", "icici",
    "axis", "kotak", "maruti", "nifty", "sensex",
    # bare durations
    "3hrs", "2hrs", "1hr", "1 hr", "days", "lot",
}
# short tokens with semantic content that beat the <=3-char fragment rule
_SEM_SHORT = {"ca", "cs", "mba", "ceo", "cfo", "cto", "coo", "hr", "md", "mp",
              "mla", "up", "ap", "mh", "cg", "goa", "nse", "bse", "1cr", "2cr",
              "10m", "how", "why", "what", "when", "who", "gst", "sip", "fno",
              "mf", "ai"}
_PURE_NUM = re.compile(r"^[\d\s.,/%+\-]+$")
_MONEY_SHORT = re.compile(r"^\d+(\.\d+)?\s?(cr|crore|l|lakh|lakhs|lpa|k|m)$", re.I)
_BARE_HRS = re.compile(r"^\d+(hr|hrs|hour|hours)$", re.I)
_BARE_DAYS = re.compile(r"^(half|\d+)\s?days?$", re.I)     # [v1.2]
_BUY = re.compile(r"\b(paid|pay|paying|payment|price|pricing|cost|fee|fees|emi|"
                  r"enroll?|enrolled|enrolment|enrollment|join|joined|joining|"
                  r"link|invoice|receipt|refund|upi|gpay|phonepe|paytm|buy|"
                  r"bought|purchase|purchased|amount|installment|instalment|"
                  r"card|debit|credit|offer|discount|register|registered|"
                  r"registration|seat|slot|batch)\b", re.I)
_PAIN = re.compile(r"\b(not work\w*|isn.?t work\w*|issue|issues|problem|error|"
                   r"unable|cannot|can.?t|couldn.?t|fail\w*|stuck|slow|worst|"
                   r"waste|fraud|scam|cheat\w*|beggar\w*|pathetic|useless|"
                   r"complain\w*|disappoint\w*)\b", re.I)
_SELF = re.compile(r"\b(\d{1,3}\s*(yrs?|years?)|lpa|salary|profession|working as|"
                   r"i am (a|an|from)|retired|housewife|student|engineer|banker|"
                   r"teacher|doctor|lawyer|manager|developer|analyst|consultant|"
                   r"founder|business\s?man|entrepreneur|freelancer)\b", re.I)
_SQUEEZE = re.compile(r"(.)\1+")


def classify(cleaned):
    """Return True if RELEVANT, False if IGNORABLE (spec sections 4-5)."""
    m = cleaned
    cf = m.casefold()
    if m == "?":
        return True
    if "?" in m:                                   # questions keep everything
        return True
    if _BUY.search(m) or _PAIN.search(m) or _SELF.search(m):
        return True
    if cf in _SEM_SHORT or _MONEY_SHORT.match(cf):
        return True
    if cf in _VOCAB_IGN:
        return False
    if _PURE_NUM.match(m) and any(c.isdigit() for c in m):
        return False                               # bare numbers/ratios/percents
    if _BARE_HRS.match(cf) or _BARE_DAYS.match(cf):
        return False                               # 3hrs / 2 days ...
    sq = _SQUEEZE.sub(r"\1", cf)                   # elongation fallback -> vocab
    if sq in _VOCAB_IGN and len(m.split()) == 1:
        return False
    if len(cf) <= 3:
        return False                               # fragments
    return True                                    # semantic default: keep


# ---------------- full pipeline for one cell ----------------
def process_cell(all_chats):
    """
    Input: the All Chats cell (str). Output dict:
      relevant, deleted (both ' | '-joined, posting order),
      stats: raw, dropped, dup_removed, rel_ct, ign_ct, strict_rel,
      lowvol (0|N -- kept for reporting; NO LONGER moves junk to Relevant).
    """
    if not all_chats or not all_chats.strip():
        return {"relevant": "", "deleted": "", "raw": 0, "dropped": 0,
                "dup_removed": 0, "rel_ct": 0, "ign_ct": 0, "strict_rel": 0,
                "lowvol": 0}
    raw_msgs = all_chats.split(SEP)                          # Stage 1
    cleaned = [clean_message(r) for r in raw_msgs]           # Stage 2
    inst = [c for c in cleaned if c]                         # Stage 3
    dropped = len(cleaned) - len(inst)
    n_inst = len(inst)

    # Stage 4 -- exact dedup on hardened key, keep first occurrence's form
    order, first_form, counts = [], {}, {}
    for c in inst:
        k = hardened_key(c)
        if k in counts:
            counts[k] += 1
        else:
            counts[k] = 1
            first_form[k] = c
            order.append(k)
    dup_removed = n_inst - len(order)

    # Stage 4b -- within-lead near-duplicate collapse (union-find)
    parent = list(range(len(order)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    keys = order
    _TRUNC = re.compile(r"\s*(\.\.\.)?\s*\[truncated\]?\s*$")
    k2 = [_TRUNC.sub("", k).rstrip(" .") for k in keys]  # cap-marker-free keys
    prefer = {}                                     # idx -> forced display form
    for i in range(len(keys)):
        ki = k2[i]
        if len(ki) < 4:
            continue
        for j in range(i + 1, len(keys)):
            kj = k2[j]
            if len(kj) < 4:
                continue
            merge = False
            # [v1.2] long prefix re-sends (incl. 32k-truncated tails): keep
            # the longer, complete variant
            if min(len(ki), len(kj)) >= 40 and (
                    ki.startswith(kj) or kj.startswith(ki)):
                merge = True
                longer = i if len(ki) >= len(kj) else j
                prefer[min(i, j)] = first_form[keys[longer]]
            elif abs(len(ki) - len(kj)) <= 0.2 * max(len(ki), len(kj)):
                sm = SequenceMatcher(None, ki, kj)
                if (sm.real_quick_ratio() >= 0.92
                        and sm.quick_ratio() >= 0.92
                        and sm.ratio() >= 0.92):    # upper bounds first
                    merge = True
            if merge:
                ra, rb = find(i), find(j)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    clusters = {}
    for idx in range(len(keys)):
        clusters.setdefault(find(idx), []).append(idx)
    survivors = []                                  # (anchor_pos, display_form)
    for root in sorted(clusters):
        members = clusters[root]
        anchor = min(members)                       # first occurrence's position
        last = max(members)                         # keep the LAST variant's text
        disp = None
        for m_ in members:                          # prefix-merge display override
            if m_ in prefer:
                disp = prefer[m_]
                break
        survivors.append((anchor, disp or first_form[keys[last]]))
        if len(members) > 1:                        # fuzzy merges join the dup tally;
            dup_removed += len(members) - 1         # exact dups already counted above
    survivors.sort(key=lambda t: t[0])

    # Stage 5 -- classify survivors.  [v1.2] STRICT SPLIT EVERYWHERE:
    # the low-volume rescue is retired; lowvol is reported but changes nothing.
    rel = [f for p, f in survivors if classify(f)]
    ign = [f for p, f in survivors if not classify(f)]
    lowvol = n_inst if 0 < n_inst <= 6 else 0

    return {"relevant": SEP.join(rel), "deleted": SEP.join(ign),
            "raw": len(raw_msgs), "dropped": dropped,
            "dup_removed": dup_removed, "rel_ct": len(rel),
            "ign_ct": len(ign), "strict_rel": len(rel), "lowvol": lowvol}


# ---------------- [v1.2] recurring anomaly audit ----------------
def audit_relevant(relevant_cells, top_n=25, freq_threshold=40):
    """
    The anomaly-detection pass. Feed it every row's Relevant Chat cell.
    Returns dict with:
      bad_chars     -- [(char, count, example)] emoji/format/control/stray
                       combining marks that survived cleaning (should be empty)
      repeats       -- [(example, kind)] whole-message repetition residue
                       (should be empty)
      vocab_leaks   -- [(msg, count)] exact _VOCAB_IGN / pure-numeric strings
                       found in Relevant (should be empty)
      freq_suspects -- [(msg, count)] NEW high-frequency identical short
                       messages across leads: candidate host-tokens for the
                       next vocab review (human judgement -- cities/professions
                       are legitimately frequent)
    """
    import unicodedata
    from collections import Counter
    bad = Counter(); bad_ex = {}; repeats = []; freq = Counter()
    for cell in relevant_cells:
        if not cell:
            continue
        for m in cell.split(SEP):
            cf = m.casefold().strip()
            if len(cf) <= 30:
                freq[cf] += 1
            for ch in m:
                cat = unicodedata.category(ch)
                if (cat in ("Cf", "Cc", "Co", "Cs")
                        or _EMOJI.match(ch)
                        or (cat == "Mn" and ord(ch) < 0x0370)):
                    bad[ch] += 1
                    bad_ex.setdefault(ch, m[:60])
            toks = [t.casefold() for t in m.split()]
            n = len(toks)
            if n >= 2:
                for p in range(1, n // 2 + 1):
                    if n % p == 0 and toks[:p] * (n // p) == toks:
                        repeats.append((m[:80], "token-period"))
                        break
            s = re.sub(r"\s+", "", cf)
            if len(s) >= 8 and _period_collapse(s) != s and not s.isdigit():
                repeats.append((m[:80], "glued"))
    leaks = [(msg, c) for msg, c in freq.items()
             if msg in _VOCAB_IGN
             or (msg and _PURE_NUM.match(msg) and any(ch.isdigit() for ch in msg))]
    suspects = [(msg, c) for msg, c in freq.most_common()
                if c >= freq_threshold and len(msg.split()) <= 2
                and (msg, c) not in leaks and msg not in _VOCAB_IGN][:top_n]
    return {"bad_chars": [(ch, c, bad_ex[ch]) for ch, c in bad.most_common()],
            "repeats": repeats[:50], "vocab_leaks": sorted(leaks, key=lambda t: -t[1]),
            "freq_suspects": suspects}


# ---------------- self-test ----------------
if __name__ == "__main__":
    ok = 0
    T = []
    for raw, want in [("readyyyyyy", "ready"), ("WWWOOWWWWWW", "WOOW"),
                      ("Realllyyyy", "Realy"), ("MBBBBBB", "MB"),
                      ("yess", "yess"), ("mee", "mee"),
                      ("freshers??", "freshers?"), ("then??????", "then?"),
                      ("Great!!!", "Great!"), ("1000xx", "1000xx"),
                      ("ready....", "ready...."),
                      ("Any  configuration", "Any configuration"),
                      ("-Are you making us pay", "Are you making us pay"),
                      ("Yes yes yes yes yes", "Yes"), ("MB MB MB MB MB", "MB"),
                      ("very very good", "very very good"),
                      ("?", "?"), ("??", ""), (":)", ""), ("👍", ""),
                      # v1.2 repetition + characters
                      ("Boom Boom", "Boom"), ("its magic its magic", "its magic"),
                      ("workshop notes link and certificate workshop notes link "
                       "and certificate workshop notes link and certificate",
                       "workshop notes link and certificate"),
                      ("MEMEMEMEMEME", "ME"),
                      ("10000x already10000x already", "10000x already"),
                      ("free video generation app?free video generation app?",
                       "free video generation app?"),
                      ("10 x 10 X 10 x", "10 x"),
                      ("10X 10 X 10X", "10X"),
                      ("MB MB MB MBMB MBMB MBMB", "MB"),
                      ("skipSkip", "skip"),
                      ("Skip skip skipSkip skip skipSkip skip", "Skip"),
                      ("2020", "2020"), ("haha", "ha"),
                      ("10x̌x", "10xx"),
                      ("it's 2'O clock ⌚", "it's 2'O clock"),
                      ("zero​width", "zerowidth"),
                      # v1.3 [bug review C3]: non-Latin scripts must SURVIVE cleaning.
                      # Every one of these returned '' before the _ALNUM range was repaired,
                      # and was dropped into neither Relevant nor Deleted.
                      ("क्या यह फ्री है?", "क्या यह फ्री है?"),          # Devanagari (Hindi)
                      ("मेरा पैसा रिटर्न कर", "मेरा पैसा रिटर्न कर"),      # the live refund demand
                      ("नमस्ते", "नमस्ते"),
                      ("दिल्ली", "दिल्ली"),                              # a city = demographics, spec 4
                      ("நன்றி", "நன்றி"),                                # Tamil
                      ("ధన్యవాదాలు", "ధన్యవాదాలు"),                      # Telugu
                      ("ಧನ್ಯವಾದಗಳು", "ಧನ್ಯವಾದಗಳು"),                      # Kannada
                      ("ধন্যবাদ", "ধন্যবাদ"),                            # Bengali
                      ("આભાર", "આભાર"),                                  # Gujarati
                      ("میں شامل ہونا چاہتا ہوں", "میں شامل ہونا چاہتا ہوں"),  # Urdu
                      ("ഫീസ് എത്ര", "ഫീസ് എത്ര"),                        # Malayalam
                      ("फीस कितनी है?", "फीस कितनी है?"),
                      ("価格は?", "価格は?"),                             # CJK
                      # mixed script must not lose either half
                      ("fees कितनी", "fees कितनी"),
                      # ...but genuinely symbol-only content must STILL drop
                      ("₹", ""), ("---", ""), ("...", ""), ("«»", ""),
                      # v1.3 [bug review H11]: real periodic words survive per-token collapse
                      ("Tata Motors", "Tata Motors"),
                      ("Papa is retired", "Papa is retired"),
                      ("Baba Ramdev", "Baba Ramdev"),
                      ("murmur", "murmur"),
                      ("tartar sauce", "tartar sauce"),
                      ("Coco Cola", "Coco Cola"),
                      # ...while junk repeats still collapse exactly as before
                      ("GAMESGAMES", "GAMES"), ("MBMB", "MB"),
                      # v1.3 [bug review M22]: format/control chars audit_relevant flags
                      # must not survive cleaning -- M12 made that audit a BLOCKING gate.
                      ("nice \U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F flag", "nice flag"),
                      ("price‮is high", "priceis high"),
                      ("price⁣high", "pricehigh"),
                      ("a⁦b⁩c", "abc")]:
        got = clean_message(raw)
        T.append((f"clean {raw!r} -> {got!r} (want {want!r})", got == want))
    for msg, want in [("20", False), ("10/10", False), ("100%", False),
                      ("965 3721 5349", False), ("ok", False), ("crm", False),
                      ("?", True), ("ca", True), ("up", True), ("1cr", True),
                      ("nse", True), ("hindi", False), ("crazy", False),
                      ("loved it", True), ("thank you", False),
                      ("payment link not working", True), ("43, Banker", True),
                      ("3hrs", False), ("1 hr", False), ("30 min", True),
                      ("2 hrs", True), ("90 Days", False), ("days", False),
                      ("What are the dates", True), ("mind blowing", True),
                      ("mind blown", False), ("tata", False),
                      ("yes sir", False), ("hyderabad", True),
                      ("18 student", True), ("paid", True),
                      # v1.2 vocab
                      ("blueprint", False), ("blue print", False),
                      ("bonus", False), ("action", False), ("agree", False),
                      ("discipline", False), ("consistency", False),
                      ("apple", False), ("reliance", False), ("amazon", False),
                      ("more", False), ("1 day", False), ("2 days", False),
                      ("apple stock is best", True),
                      ("action plan for my business", True)]:
        got = classify(msg)
        T.append((f"classify {msg!r} -> {got} (want {want})", got == want))
    r = process_cell("mb | MB | MB | bonus")
    T.append(("dedup mb x3 -> 2 removed", r["dup_removed"] == 2))
    T.append(("strict: junk-only row -> Relevant blank",
              r["relevant"] == "" and r["deleted"] == "mb | bonus"))
    r = process_cell("What are the dates | What are the dates? | filler one two "
                     "| filler three four | filler five six | filler seven8 nine")
    T.append(("hardened key merges '?-variant'", r["dup_removed"] == 1))
    r = process_cell(SEP.join(["ai tool for trading", "ai tools for trading",
                               "pad msg one x", "pad msg two y", "pad msg three z",
                               "pad msg four w", "pad msg five v"]))
    T.append(("fuzzy 4b merges, keeps LAST variant",
              "ai tools for trading" in r["relevant"]
              and "ai tool for trading" not in r["relevant"]))
    r = process_cell("i have paid | can you tell me the duration | 31 | clear | me")
    T.append(("strict split on small chatter: junk -> Deleted",
              r["relevant"] == "i have paid | can you tell me the duration"
              and r["deleted"] == "31 | clear | me" and r["lowvol"] == 5))
    r = process_cell("👍")
    T.append(("emoji-only lead: both blank",
              r["relevant"] == "" and r["deleted"] == "" and r["lowvol"] == 0))
    full = ("you told in the youtube ad that you will share a free website "
            "that can generate free ai videos, that is why i joined")
    r = process_cell(SEP.join([full, full[:80] + "...[truncated]"]))
    T.append(("prefix/truncated re-send merges, keeps FULL variant",
              r["relevant"] == full and r["dup_removed"] == 1))
    a = audit_relevant(["payment not working | i have paid",
                        "mb | what is the price", "Boom Boom again Boom Boom again"])
    T.append(("audit catches vocab leak 'mb'",
              any(m == "mb" for m, c in a["vocab_leaks"])))
    T.append(("audit catches token-period residue",
              any(k == "token-period" for _, k in a["repeats"])))
    # v1.3 [bug review M22]: bad_chars was never asserted in EITHER direction, which is why
    # _FMTCTL was allowed to drift narrower than the audit. Both directions now pinned.
    T.append(("audit flags a raw format char",
              len(audit_relevant(["price‮is high"])["bad_chars"]) > 0))
    _hostile = ["nice \U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F flag",
                "price‮is high", "price⁣high", "a⁦b⁩c", "​zero​width"]
    T.append(("cleaned output leaves the audit clean (cleaner and audit agree)",
              len(audit_relevant([SEP.join(x for x in (clean_message(h) for h in _hostile) if x)])["bad_chars"]) == 0))
    for name, passed in T:
        ok += passed
        if not passed:
            print("FAIL:", name)
    print(f"{ok}/{len(T)} passed")
