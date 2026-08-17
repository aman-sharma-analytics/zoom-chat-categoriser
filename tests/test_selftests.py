# -*- coding: utf-8 -*-
"""Fast tests that need no real workshop data. Run:  python -m tests.test_selftests
(also collectable by pytest). Nothing else in the project may be trusted until these pass."""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
PY = sys.executable


def test_chat_cleaner_selftest():
    """The verbatim copy must pass its own 123-case suite."""
    out = subprocess.run([PY, os.path.join(HERE, 'core', 'chat_cleaner.py')],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    m = re.search(r'(\d+)/(\d+) passed', out.stdout or '')
    assert m and m.group(1) == m.group(2), "chat_cleaner self-test failed:\n%s" % out.stdout
    assert int(m.group(2)) >= 123


def test_contact_norm_selftest():
    out = subprocess.run([PY, os.path.join(HERE, 'core', 'contact_norm.py')],
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
    assert out.returncode == 0, "contact_norm self-test failed:\n%s" % out.stdout
    m = re.search(r'self-test: (\d+)/(\d+) passed', out.stdout or '')
    assert m and m.group(1) == m.group(2)


def test_config_compiles_and_empty_bank_refused():
    from core.dimensions import Scorer, compile_cfg, load_config
    cfg = load_config(os.path.join(HERE, 'config', 'phrase_banks.json'))
    Scorer(cfg)                       # compiles all banks + weights
    bad = {k: (dict(v) if isinstance(v, dict) else list(v)) for k, v in cfg.items()
           if k in ('phrase_banks', 'negativity_patterns', 'noise_exact', 'buying_categories', 'dimensions')}
    bad['phrase_banks'] = dict(bad['phrase_banks'], pricing=[])
    try:
        compile_cfg(bad)
        raise AssertionError("an empty phrase bank must be refused (re.compile('') matches everything)")
    except ValueError:
        pass


def test_nul_row_costs_one_row_not_a_room():
    from core.parse_attendee import aggregate_text
    csv_text = (
        "Attendee Report\n"
        "Topic,Webinar ID,Actual Start Time,Actual Duration (minutes),# Registrants\n"
        'T,1,"08/02/2026 10:45:00 AM",225,3\n'
        "Attendee Details\n"
        "Attended,User Name (Original Name),First Name,Last Name,Email,Phone,Registration Time,Approval Status,Join Time,Leave Time,Time in Session (minutes),Is Guest,Country/Region Name\n"
        'Yes,A One,A,One,a1@gmail.com,9845344750,,,"08/02/2026 10:50:00 AM","08/02/2026 11:50:00 AM",60,Yes,India\n'
        'Yes,B\x00 Two,B,Two,b2@gmail.com,9845344751,,,"08/02/2026 10:50:00 AM","08/02/2026 11:50:00 AM",60,Yes,India\n'
        'Yes,C Three,C,Three,c3@gmail.com,9845344752,,,"08/02/2026 10:50:00 AM","08/02/2026 11:50:00 AM",60,Yes,India\n'
    )
    recs, meta, st = aggregate_text(csv_text)
    assert len(recs) == 3, "a NUL byte must cost at most one row, never the rows after it"
    assert st.get('nul') == 1
    assert 'b2@gmail.com' in recs, "the NUL row itself should be repaired and kept"


def test_midnight_wrap():
    from core.parse_chat import parse_chat_text
    blocks, n = parse_chat_text(
        "23:58:00 From A to Everyone:\n\tlate\n"
        "00:02:00 From A to Everyone:\n\tafter midnight\n")
    assert n == 2
    assert blocks[1]['sec'] - blocks[0]['sec'] == 240, "clock wrap must add 24h"


def test_reply_quotes_never_credited():
    from core.parse_chat import parse_chat_text
    blocks, _ = parse_chat_text(
        '10:00:00 From A to Everyone:\n'
        '\tReplying to "someone else\'s words"\n'
        '\tmy actual reply\n')
    assert blocks[0]['lines'] == ['my actual reply']


def test_script_survival_and_word_mangling_through_pipeline():
    from core import pipeline
    csv_text = (
        "Attendee Report\n"
        "Topic,Webinar ID,Actual Start Time,Actual Duration (minutes),# Registrants\n"
        'T,1,"08/02/2026 10:45:00 AM",225,2\n'
        "Attendee Details\n"
        "Attended,User Name (Original Name),First Name,Last Name,Email,Phone,Registration Time,Approval Status,Join Time,Leave Time,Time in Session (minutes),Is Guest,Country/Region Name\n"
        'Yes,Ravi Kumar,Ravi,Kumar,ravik9382@gmail.com,9845344750,,,"08/02/2026 10:50:00 AM","08/02/2026 02:10:00 PM",200,Yes,India\n'
        'Yes,Tata Fan,Tata,Fan,tatafan1988@gmail.com,9812345670,,,"08/02/2026 10:50:00 AM","08/02/2026 02:10:00 PM",200,Yes,India\n'
    )
    chat_text = (
        "10:55:01 From Ravi Kumar to Everyone:\n\tक्या यह फ्री है?\n"
        "10:56:02 From Ravi Kumar to Everyone:\n\tमेरा पैसा रिटर्न कर\n"
        "10:57:03 From Ravi Kumar to Everyone:\n\tநன்றி\n"
        "10:58:04 From Ravi Kumar to Everyone:\n\tధన్యవాదాలు\n"
        "10:59:05 From Ravi Kumar to Everyone:\n\tمیں شامل ہونا چاہتا ہوں\n"
        "11:00:06 From Tata Fan to Everyone:\n\tTata Motors is my company\n"
        "11:01:07 From Tata Fan to Everyone:\n\tPapa is retired and murmur\n"
    )
    rooms = {'root': {'attendee_texts': [csv_text], 'chat_texts': [chat_text], 'files': []}}
    res = pipeline.process_session(rooms)
    by = {r['email']: r for r in res['rows']}
    rel = by['ravik9382@gmail.com']['relevant']
    for needle in ("क्या यह फ्री है?", "मेरा पैसा रिटर्न कर", "நன்றி", "ధన్యవాదాలు", "میں شامل ہونا چاہتا ہوں"):
        assert needle in rel, "non-Latin message vanished from Relevant Chat: %r" % needle
    trel = by['tatafan1988@gmail.com']['relevant']
    assert "Tata Motors" in trel and "Ta Motors" not in trel
    assert "Papa is retired" in trel and "murmur" in trel
    assert res['audit'] == {'bad_chars': 0, 'repeats': 0, 'vocab_leaks': 0}
    assert not res.get('gate_failed')


def test_categorize_smoke():
    from core.categorize import categorize_person, CAT_PIH, CAT_NC, CAT_NEG
    dims0 = {'D1': 0, 'D3': 0, 'D4': 0, 'D5': 0, 'D7': 0}
    c0 = {'intent': 0, 'mean': 0, 'neg': 0}
    cat, _ = categorize_person("X", False, 50.0, None, None, dims0, c0, [], orphan=False, no_text=False)
    assert cat == CAT_NC
    cat, _ = categorize_person("X", True, 80.0, None, None, dims0,
                               {'intent': 1, 'mean': 2, 'neg': 0},
                               ["i have paid for the program", "when does the batch start?"])
    assert cat == CAT_PIH
    cat, _ = categorize_person("X", True, 80.0, None, None, dims0,
                               {'intent': 0, 'mean': 2, 'neg': 2},
                               ["this is a complete scam", "waste of time and money"])
    assert cat == CAT_NEG


if __name__ == '__main__':
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, "--", e)
    print("%s" % ("ALL PASS" if not fails else "%d FAILURE(S)" % fails))
    raise SystemExit(1 if fails else 0)
