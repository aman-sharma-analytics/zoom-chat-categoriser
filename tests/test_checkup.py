# -*- coding: utf-8 -*-
"""Regression tests for the bugs found in the 2026-09-03 whole-tool checkup.

Run:  python -m tests.test_checkup
No workshop data needed -- everything here is synthetic.

Each test names the defect it locks down. Two of them (cc_split, all_chats) assert
DELIBERATE behaviour that reads like a bug and must not be "fixed" without the evidence
recorded in tests/GOLDEN_NOTES.md.
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import parse_attendee as PA          # noqa: E402
from core import preflight, pipeline           # noqa: E402
from core.categorize import categorize_person, confidence_for   # noqa: E402

NUL = chr(0)
D0 = {"D1": 0, "D3": 0, "D4": 0, "D5": 0, "D7": 0}

ATT_HDR = ("Attended,User Name (Original Name),First Name,Last Name,Email,Phone,"
           "Registration Time,Approval Status,Join Time,Leave Time,"
           "Time in Session (minutes),Is Guest,Country/Region Name")
ATT_ROW = ('Yes,Asha Rao,Asha,Rao,asha.rao77@gmail.com,9845344750,,,'
           '"08/30/2026 11:05:00 AM","08/30/2026 02:30:00 PM",200,Yes,India')
CHAT = ("11:10:01 From Asha Rao to Everyone:\n\thello everyone\n"
        "13:50:01 From Asha Rao to Everyone:\n\twhat is the fees\n")


def _cat(msgs, dims=None, mm=None, im=0, name="Test Person", cta=None, pricing=None,
         full=True, pct=80.0):
    cnt = {"intent": im, "mean": len(msgs) if mm is None else mm, "neg": 0}
    cat, basis = categorize_person(name, True, pct, cta, pricing, dict(D0, **(dims or {})),
                                   cnt, msgs if full else None)
    return cat, basis, confidence_for(cat, basis, full=full)


# ---------------------------------------------------------------- confidence_for
def test_pih_on_a_full_corpus_is_always_high():
    """A price-corroborated PIH used to publish 'medium'.

    The engine appends 'program price ask' AFTER the PIH return, so that anchor never
    reaches a PIH basis. v4.6 P1 guarantees every PIH gate rests on a text anchor once the
    corpus is full, so the category alone settles it.
    """
    cat, basis, conf = _cat(["what is the fees", "kab tak course milega"], im=2, mm=2)
    assert cat == "purchase intent high", cat
    assert "program price ask" not in basis, basis      # the gap this works around
    assert conf == "high", (conf, basis)


def test_negated_buying_language_does_not_grade_on_the_phrase_it_denies():
    """'no buying language' must not substring-match the 'buying language' MED tag."""
    cat, basis, conf = _cat(["how does the excel automation work?"], mm=1)
    assert cat == "information seeking", cat
    assert ", no buying language" in basis, basis
    # graded on the negation-stripped copy: the level may be medium, but never BECAUSE of
    # the negated phrase -- prove it by checking the same basis minus the denial
    assert conf == confidence_for(cat, basis.replace(", no buying language", "")), conf


def test_presence_driven_moderate_interest_is_not_low():
    """The late-rescue MI basis says 'meaningful msg(s)'; the tag said 'meaningful msgs',
    so a chatter present at pricing AND staying to CTA fell through to 'low'."""
    cat, basis, conf = _cat(["the automation runs on the sheet directly"], mm=2,
                            cta=True, pricing=True)
    assert cat == "moderate interest", (cat, basis)
    assert "meaningful msg(s)" in basis, basis
    assert conf == "medium", (conf, basis)


def test_bot_display_name_cannot_steer_confidence():
    """The P7 basis interpolates the raw display name, so name text used to reach the
    low-first string tests. It is a rule: always high, whatever the name says."""
    for nm in ("Fireflies.ai Notetaker", "no chat messages bot notetaker",
               "label capped notetaker", "otter.ai fees paid discount ask notetaker"):
        cat, basis, conf = _cat(["hello"], name=nm)
        assert cat == "no clear intent", (nm, cat)
        assert basis.startswith("bot/notetaker display name ("), basis
        assert conf == "high", (nm, conf)


def test_confidence_levels_that_must_not_move():
    assert confidence_for("not attended", "did not join") == "low"
    assert confidence_for("no clear intent", "no chat messages; barely present") == "low"
    assert confidence_for("purchase intent high", "2 intent msgs", full=False) == "low"
    assert confidence_for("no clear intent", "x; label capped") == "low"
    assert confidence_for("moderate interest", "3 meaningful msgs", no_text=True) == "low"
    # praise/tech-only NCI stays medium; the bare catch-all stays low
    assert confidence_for("no clear intent",
                          "only praise/AV/generic chat, no substantive signal") == "medium"
    assert confidence_for("no clear intent",
                          "only greetings/generic chat, no substantive signal") == "low"


# ---------------------------------------------------------------- preflight
def test_zero_row_csv_with_chat_does_not_crash_preflight():
    """check_rooms read `dur` before binding it when a CSV parsed 0 rows and chat existed,
    dying with UnboundLocalError and taking the whole job with it."""
    rooms = {'Zoom 1': {'attendee_texts': ["Attendee Report\r\nAttendee Details\r\n" + ATT_HDR + "\r\n"],
                        'chat_texts': [CHAT], 'files': []}}
    v = preflight.check_rooms(rooms)[0]
    assert v['level'] == 'FAIL', v
    assert any('parsed 0 rows' in m for m in v['messages']), v['messages']


def test_duration_does_not_leak_between_rooms():
    """A room whose CSV parses 0 rows must fall back to the default duration, not inherit
    the previous room's -- the truncated-chat verdict is computed from it."""
    good = ("Attendee Report\r\n"
            "Topic,Webinar ID,Actual Start Time,Actual Duration (minutes),# Registrants\r\n"
            'T,1,"08/30/2026 11:00:00 AM",20,1\r\n'
            "Attendee Details\r\n" + ATT_HDR + "\r\n" + ATT_ROW + "\r\n")
    rooms = {
        'A room': {'attendee_texts': [good], 'chat_texts': [CHAT], 'files': []},
        'B room': {'attendee_texts': ["Attendee Report\r\nAttendee Details\r\n" + ATT_HDR + "\r\n"],
                   'chat_texts': [CHAT], 'files': []},
    }
    out = {v['room']: v for v in preflight.check_rooms(rooms)}
    b = out['B room']['messages']
    # A room's 20-minute duration must not be what B's chat span is judged against
    assert not any('~20 min' in m for m in b), b


# ---------------------------------------------------------------- attendee parsing
def test_header_discovery_survives_nul_bytes():
    """A NUL inside 'Attended,' failed both startswith tests, so the header was never
    found and every attendee in the room was discarded as 'parsed 0 rows'."""
    for hdr in (ATT_HDR,
                ATT_HDR.replace("Attended", "Atte" + NUL + "nded", 1),
                NUL + ATT_HDR,
                ATT_HDR.replace("Email", "Em" + NUL + "ail", 1),
                ATT_HDR.replace(",", NUL + ",", 3)):
        lines = ["Attendee Report", "Attendee Details", hdr, ATT_ROW]
        rows = PA.parse_attendee_rows(lines, {})
        assert len(rows) == 1, (len(rows), repr(hdr[:30]))
        assert rows[0].get('Email') == 'asha.rao77@gmail.com', rows[0]


def test_nul_header_still_prefers_the_attendee_section_over_host():
    lines = ["Attendee Report", "Host Details",
             "Attended,User Name (Original Name),Email,Time in Session (minutes)",
             "Yes,Host Person,host@example.com,10",
             "Attendee Details", NUL + ATT_HDR, ATT_ROW]
    rows = PA.parse_attendee_rows(lines, {})
    assert len(rows) == 1 and rows[0]['Email'] == 'asha.rao77@gmail.com', rows


# ---------------------------------------------------------------- pipeline contracts
def test_blank_activity_date_is_warned_about():
    """No date entered and no parseable 'Actual Start Time' used to publish a silently
    blank Activity Date on every row."""
    rooms = {'root': {'attendee_texts': ["Attendee Report\r\nAttendee Details\r\n" + ATT_HDR
                                         + "\r\n" + ATT_ROW.replace(
                                             '"08/30/2026 11:05:00 AM"', '--') + "\r\n"],
                      'chat_texts': [], 'files': []}}
    res = pipeline.process_session(rooms, activity_date="", session_name="X")
    assert any('Activity Date is BLANK' in w for w in res['warnings']), res['warnings']


def test_cc_split_keeps_ten_digit_numbers_whole():
    """DELIBERATE, not a bug. Zoom's Country column records where the person LIVES, so an
    NRI's 10-digit Indian mobile arrives tagged with a foreign country. Peeling a dial code
    off a 10-digit number would truncate 31 of the 33 real cases in the 11 archived
    sessions (measured 2026-09-03). Only numbers LONGER than 10 digits are split.
    """
    # foreign number that genuinely carries its dial code -> split
    assert pipeline.cc_split("United Arab Emirates", "971506791561") == ("+971", "506791561")
    assert pipeline.cc_split("United Kingdom", "447911123456") == ("+44", "7911123456")
    # 10-digit Indian mobile on a foreign-country row -> kept whole, dial code prefixed
    assert pipeline.cc_split("Singapore", "6597436884") == ("+65", "6597436884")
    assert pipeline.cc_split("United Arab Emirates", "9711947200") == ("+971", "9711947200")
    # India's own default path
    assert pipeline.cc_split("India", "919845344750") == ("+91", "9845344750")
    assert pipeline.cc_split("India", "9845344750") == ("+91", "9845344750")
    assert pipeline.fmt_phone("", "") == ""
    assert pipeline.fmt_phone("+91", "") == ""


def test_blank_country_does_not_stamp_plus91_on_an_international_number():
    """With the Country cell empty nothing peeled the dial code, so the same UAE number
    published as '+971-545512993' with the country filled and '+91-971545512993' without
    it. 101 blank-country rows in the archived exports were in that state."""
    assert pipeline.cc_split("", "971545512993") == ("+971", "545512993")
    assert pipeline.cc_split("", "447911123456") == ("+44", "7911123456")
    assert pipeline.cc_split("", "923148669995") == ("+92", "3148669995")
    # 11 digits with no country is ambiguous -- a mistyped Indian mobile is as likely as a
    # foreign number, so every digit is kept rather than one being peeled away
    assert pipeline.cc_split("", "94617772936") == ("+91", "94617772936")
    assert pipeline.cc_split("", "9845344750") == ("+91", "9845344750")
    # a named country is better evidence than the digits: never peel past it
    assert pipeline.cc_split("India", "94617772936") == ("+91", "94617772936")


def test_every_country_in_the_real_exports_has_a_dial_code():
    """An unmapped country fell through to +91. These 64 names appear in the 11 archived
    exports; all of them were missing (223 rows)."""
    for name in ("Angola", "Togo", "Croatia", "Slovakia", "Senegal", "Botswana",
                 "Maldives", "Lithuania", "Latvia", "Czech Republic", "Rwanda", "Malta",
                 "Iraq", "Myanmar", "Taiwan", "Ukraine", "Cambodia", "Armenia",
                 "Congo, Democratic Republic of the", "Korea, Republic of"):
        assert name in pipeline.COUNTRY_CC, name
        cc = pipeline.COUNTRY_CC[name]
        assert cc.isdigit() and 1 <= len(cc) <= 3, (name, cc)


def test_published_phone_never_loses_a_digit():
    """The invariant behind every phone decision above: whatever cc_split does, the
    published value must still contain the stored number in full. Verified across all
    599,848 real phone rows (0 losses) on 2026-09-03."""
    import re
    for country, d in [("", "971545512993"), ("India", "9845344750"),
                       ("India", "94617772936"), ("Singapore", "6597436884"),
                       ("United Arab Emirates", "9711947200"), ("Angola", "244923500094"),
                       ("Aruba", "9845700618"), ("", "447911123456"),
                       ("Hong Kong", "85291234567"), ("Nepal", "9779852027842")]:
        out = pipeline.fmt_phone(*pipeline.cc_split(country, d))
        assert d in re.sub(r"\D", "", out), (country, d, out)


# ---------------------------------------------------------------- broken Zoom timing
def test_inverted_segment_reads_as_unknown_not_zero_percent():
    """Zoom exports some rows with leave BEFORE join (and a negative 'Time in Session' of
    its own). merged_minutes already drops them, so they used to arrive as a confident
    0 minutes / 0% attended -- the exact failure pct_attended exists to prevent. 107 people
    across the 11 archived sessions have no other kind of segment (measured 2026-09-03).
    """
    import datetime
    D = datetime.datetime
    meta = {'duration_min': 200.0}

    def rec_for(segs):
        return {'seg_list': segs, 'first_join': (segs[0][0] if segs else None),
                'attended_flag': True, 'minutes_present': PA.merged_minutes(segs)}

    inv = [(D(2026, 7, 12, 13, 47), D(2026, 7, 12, 10, 47))]
    good = [(D(2026, 7, 12, 11, 0), D(2026, 7, 12, 13, 0))]
    assert PA.pct_attended(rec_for(inv), meta) is None            # unknown, not 0.0
    assert PA.pct_attended(rec_for([]), meta) is None
    # a usable segment alongside a broken one still decides it
    assert PA.pct_attended(rec_for(inv + good), meta) == 60.0
    assert PA.pct_attended(rec_for(good), meta) == 60.0
    assert PA.merged_minutes(inv) == 0.0


def _two_ravis(second_row_times):
    """One room, two contacts sharing the display name 'Ravi Kumar'."""
    from core import parse_chat
    from core import attribute
    rows = [
        'Yes,Ravi Kumar,Ravi,Kumar,ravi.one@gmail.com,9845344750,,,'
        '"08/30/2026 11:00:00 AM","08/30/2026 02:00:00 PM",180,Yes,India',
        'Yes,Ravi Kumar,Ravi,Kumar,ravi.two@gmail.com,9812345670,,,'
        + second_row_times + ',Yes,India',
    ]
    text = ("Attendee Report\r\n"
            "Topic,Webinar ID,Actual Start Time,Actual Duration (minutes),# Registrants\r\n"
            'T,1,"08/30/2026 11:00:00 AM",200,2\r\n'
            "Attendee Details\r\n" + ATT_HDR + "\r\n" + "\r\n".join(rows) + "\r\n")
    recs, meta, _st = PA.aggregate_text(text)
    chat = "12:30:00 From Ravi Kumar to Everyone:\n\twhat is the fees for the program\n"
    blocks, _nh = parse_chat.parse_chat_text(chat)
    return attribute.build_room_attribution(recs, meta, blocks)


def test_same_name_chat_is_not_guessed_past_a_blind_twin():
    """A same-name candidate who cannot be placed in time at all is UNKNOWN, not absent.
    The presence window must not rule them out and hand the message to the other contact:
    16 of the 16,456 real same-name groups are in exactly this state."""
    # twin's segment is INVERTED -> unplaceable -> nobody may be credited
    res = _two_ravis('"08/30/2026 01:47:00 PM","08/30/2026 10:47:00 AM",-180')
    credited = {k: v for k, v in (res['by_key'] or {}).items() if v}
    assert not credited, ("credited past a blind twin: %r" % (credited,))
    assert res['unattr'], "the message must be kept as ambiguous against both candidates"
    assert res['stats']['msg_still_ambiguous'] == 1, dict(res['stats'])

    # control: twin has REAL timing that excludes the message time -> resolution is sound
    res2 = _two_ravis('"08/30/2026 09:00:00 AM","08/30/2026 09:30:00 AM",30')
    credited2 = {k: v for k, v in (res2['by_key'] or {}).items() if v}
    # key_email canonicalises gmail addresses: dots in the local part are insignificant
    assert list(credited2) == ['ravione@gmail.com'], (list(credited2), dict(res2['stats']))
    assert res2['stats']['msg_time_resolved'] == 1, dict(res2['stats'])


TESTS = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]


def main():
    bad = 0
    for fn in TESTS:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL %s -- %s" % (fn.__name__, e))
    print("ALL PASS" if not bad else "%d FAILED" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
