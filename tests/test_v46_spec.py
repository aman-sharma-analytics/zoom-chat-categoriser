# -*- coding: utf-8 -*-
"""The 20 acceptance cases from the v4.6 update spec (STEP 9), plus the Confidence contract.

Run:  python -m tests.test_v46_spec
No workshop data needed -- these are synthetic rows against categorize_person().

Where the spec's prose and its own formula disagree, the FORMULA and the engine win, and
the case notes say so (see case 16).
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core.categorize import categorize_person, confidence_for   # noqa: E402

D0 = {"D1": 0, "D3": 0, "D4": 0, "D5": 0, "D7": 0}


def run(msgs, dims=None, mm=None, im=0, neg=0, name="Test Person", chatted=True,
        pct=80.0, cta=None, pricing=None, full=True, no_text=False):
    """Categorise one synthetic person. mm defaults to len(msgs)."""
    d = dict(D0, **(dims or {}))
    counts = {"intent": im, "mean": len(msgs) if mm is None else mm, "neg": neg}
    return categorize_person(name, chatted, pct, cta, pricing, d, counts,
                             None if not full else msgs, no_text=no_text)


CASES = [
    # (n, description, msgs, kwargs, expected)
    (1, "P3 typo tolerance: 'payment donef'",
     ["payment donef"], {}, "purchase intent high"),
    (2, "payment done + course-detail ask",
     ["payment done", "kab tak course milega"], {}, "purchase intent high"),
    (3, "TOOLPRICE credits clause -- must NEVER be purchase intent",
     ["my chatgpt credits khatam ho gaye, renew kaise kare"], {}, "information seeking"),
    (4, "tool-cost question alone, not program intent",
     ["is canva free or paid?"], {}, "information seeking"),
    (5, "P4 EMIASK Hinglish 'kisht'",
     ["kisht me payment ho sakta hai kya"], {}, "purchase intent high"),
    (6, "EMIASK first-month clause",
     ["1st month me kitna payment karna hoga"], {}, "purchase intent high"),
    (7, "P2 ABUSEHI veto -- the price mention must NOT promote",
     ["bhosdike loot rahe ho, 9 rupees ka scam"], {"mm": 1}, "negative engagement"),
    (8, "pay/frict rows are EXEMPT from the abuse veto",
     ["payment failed ho gaya, UPI se ho sakta hai?", "bhosdike"], {}, "purchase intent high"),
    (9, "P5 RITUALPHRASE -- 4 ritual lines, mm=4, must NOT reach moderate interest",
     ["yes sir definitely 100%", "pakka ji", "noted sir", "sure surely"], {"mm": 4},
     "no clear intent"),
    (10, "P6 ENTFIT -> strong interest",
     ["we want to implement this in our firm, does company license allow?"], {},
     "strong interest"),
    (11, "P7 bot/notetaker display name",
     ["anything at all", "second message"], {"name": "Fireflies.ai Notetaker"},
     "no clear intent"),
    (12, "tech-only chat",
     ["audio not clear", "going too fast", "can't hear"], {}, "no clear intent"),
    (13, "single scam message, mm=1",
     ["this is a scam"], {"mm": 1}, "negative engagement"),
    (14, "question-topic scam + engaged asker -- must NOT be NEG",
     ["is this a scam or genuine? asking because I was cheated before",
      "how does the excel automation work?",
      "can we use it for reports?",
      "what tools are covered?"], {"mm": 4}, None),   # asserted as "not NEG" below
    (15, "how to join + fees",
     ["how do I join, what are the fees"], {}, "purchase intent high"),
    (16, "price alone -> strong interest (spec STEP 6 formula excludes price from PIH; "
         "spec STEP 1 prose saying 'price+' is loose -- engine line agrees with the formula)",
     ["fees kitni hai?"], {}, "strong interest"),
    (17, "P1 no text anchor: dims alone must NOT reach PIH",
     ["the dashboard looks nice"], {"dims": {"D1": 10, "D3": 2}, "mm": 1}, None),  # not PIH
    (18, "P1 evidence-split fallback IS an anchor -> PIH",
     ["strongest evidence fragment"], {"dims": {"D1": 10, "D3": 2}, "mm": 1, "full": False},
     "purchase intent high"),
    (19, "existing-customer grievance",
     ["I paid earlier in 2024, never got access"], {"mm": 1}, "negative engagement"),
    (20, "deliberation -> not NEG",
     ["need a day to think about it", "what is the batch size?"], {}, None),   # not NEG
]


def main():
    fails = []
    print("=== v4.6 spec acceptance cases ===")
    for n, desc, msgs, kw, expected in CASES:
        cat, basis = run(msgs, **kw)
        if n == 14 or n == 20:
            ok = cat != "negative engagement"
            want = "NOT negative engagement"
        elif n == 17:
            ok = cat != "purchase intent high"
            want = "NOT purchase intent high"
        else:
            ok = cat == expected
            want = expected
        if not ok:
            fails.append((n, desc, want, cat, basis))
        print("  %-4s %2d  %-24s got=%s" % ("ok" if ok else "FAIL", n, want[:24], cat))
        if not ok:
            print("        desc : %s" % desc)
            print("        basis: %s" % basis[:150])

    # ---- Confidence contract (spec STEP 8)
    print("\n=== Confidence contract ===")
    conf_cases = [
        ("text anchor -> high", ["payment done"], {}, "high"),
        ("bot rule -> high", ["hi"], {"name": "Fireflies.ai Notetaker"}, "high"),
        ("dim/count driven -> medium", ["the dashboard looks nice", "and the excel one",
                                        "plus the resume tool"], {"mm": 3}, "medium"),
        ("non chatted -> low", [], {"chatted": False}, "low"),
    ]
    for desc, msgs, kw, want in conf_cases:
        cat, basis = run(msgs, **kw)
        got = confidence_for(cat, basis, full=kw.get("full", True),
                             no_text=kw.get("no_text", False))
        ok = got == want
        if not ok:
            fails.append((0, desc, want, got, basis))
        print("  %-4s %-28s want=%-7s got=%-7s (%s)" % ("ok" if ok else "FAIL", desc, want, got, cat))

    print()
    if fails:
        print("%d FAILURE(S)" % len(fails))
        return 1
    print("ALL PASS (%d spec cases + %d confidence cases)" % (len(CASES), len(conf_cases)))
    return 0


# pytest entry points
def test_v46_spec_cases():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
