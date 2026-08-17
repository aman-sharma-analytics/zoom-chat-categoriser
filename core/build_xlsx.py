# -*- coding: utf-8 -*-
"""XLSX writer for the categoriser deliverable.

7 columns in the agreed order (extras optional, off by default). Written with
xlsxwriter and constant_memory=False -- that produces a real shared-strings table;
inline-strings mode bloats the file and some tools mishandle it.

The writer re-asserts the §7 invariants and refuses to write a build whose
chat-clean audit gate failed: junk in Relevant Chat must block the download,
not ship with a warning.
"""
import re

import xlsxwriter

from .categorize import CAT_NA, CAT_NC, CAT_ORDER
from .pipeline import GateError

ILLEGAL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def clean(v):
    return ILLEGAL.sub('', v) if isinstance(v, str) else v


BASE_COLS = [
    ("Customer name", 'name', 26),
    ("Customer email", 'email', 30),
    ("Country code", 'cc', 9),
    ("Phone number", 'phone', 15),
    ("Engagement category", 'category', 20),
    ("Relevant chat", 'relevant', 60),
    ("Removed chat", 'deleted', 40),
]
EXTRA_COLS = [
    ("Category basis", 'basis', 40),
    ("Zoom room", 'room', 12),
    ("Attended?", 'attended', 9),
    ("Minutes present", 'minutes', 10),
    ("Message count", 'msg_count', 9),
]


def write_xlsx(result, path, extras=False):
    """result: pipeline.process_session output. Raises GateError when blocked."""
    if result.get('gate_failed'):
        raise GateError(result['gate_failed'])

    rows = result['rows']
    # §7 invariants, re-checked at the writer boundary (cheap, and the last line of defence)
    allowed = set(CAT_ORDER)
    for r in rows:
        assert r['category'] in allowed
        if r['category'] in (CAT_NC, CAT_NA):
            assert not r['relevant'] and not r['deleted']
        if r['relevant'] or r['deleted']:
            assert r['all_chats']

    cols = BASE_COLS + (EXTRA_COLS if extras else [])
    wb = xlsxwriter.Workbook(path, {'constant_memory': False, 'strings_to_urls': False,
                                    'strings_to_formulas': False})
    H = wb.add_format({'bold': True, 'bg_color': '#1F3864', 'font_color': 'white',
                       'border': 1, 'text_wrap': True, 'valign': 'vcenter'})
    wrap = wb.add_format({'text_wrap': True, 'valign': 'top'})
    top = wb.add_format({'valign': 'top'})
    txt = wb.add_format({'valign': 'top', 'num_format': '@'})   # phone must never be a float

    ws = wb.add_worksheet("Leads")
    for c, (h, _k, w) in enumerate(cols):
        ws.set_column(c, c, w)
        ws.write(0, c, h, H)
    wrap_keys = {'relevant', 'deleted', 'basis'}
    txt_keys = {'phone', 'cc'}
    wrfail = 0
    for i, r in enumerate(rows, start=1):
        for c, (_h, k, _w) in enumerate(cols):
            fmt = wrap if k in wrap_keys else (txt if k in txt_keys else top)
            if ws.write(i, c, clean(r.get(k, "")), fmt):
                wrfail += 1
    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, len(rows), len(cols) - 1)

    # ---- Run Report sheet: how the build went, in the file itself
    rs = wb.add_worksheet("Run Report")
    rs.set_column(0, 0, 42)
    rs.set_column(1, 1, 110)
    B = wb.add_format({'bold': True})
    r_ = 0

    def line(a, b=""):
        nonlocal r_
        rs.write(r_, 0, clean(str(a)), B if b == "" else None)
        if b != "":
            rs.write(r_, 1, clean(str(b)), wrap)
        r_ += 1

    line("Zoom Chat Categoriser -- run report")
    r_ += 1
    line("Rows (unique people across all rooms)", len(rows))
    for cat in CAT_ORDER:
        if result['category_counts'].get(cat):
            line("  " + cat, result['category_counts'][cat])
    r_ += 1
    line("Rooms")
    for room, st in result['room_stats'].items():
        line("  " + room, "people=%s attended=%s chat_blocks=%s span=%smin/%smin nul_rows=%s%s" % (
            st.get('people'), st.get('attended'), st.get('chat_blocks'),
            st.get('chat_span_min', '?'), st.get('duration_min', '?'), st.get('nul_rows', 0),
            " [NO USABLE ATTENDEE REPORT]" if st.get('lost') else ""))
    r_ += 1
    a = result['attribution']
    line("Chat attribution")
    line("  senders resolved by unique name", a['senders_unique'])
    line("  senders resolved by presence window", a['senders_window_resolved'])
    line("  senders unresolved", a['senders_unresolved'])
    line("  senders with no attendee-report row", a['senders_no_csv_row'])
    line("  messages attributed", a['msgs_attributed'])
    line("  messages dropped (ambiguous/unmatchable)", "%s (%s%%) -- dropped rather than guessed, by design"
         % (a['msgs_dropped'], a['msgs_dropped_pct']))
    r_ += 1
    line("Chat-clean audit (must be 0/0/0)", "bad_chars=%(bad_chars)d repeats=%(repeats)d vocab_leaks=%(vocab_leaks)d"
         % result['audit'])
    if result.get('pricing_offset') is not None or result.get('cta_offset') is not None:
        line("Offsets used", "pricing=%s min, CTA=%s min (10-minute windows)"
             % (result.get('pricing_offset'), result.get('cta_offset')))
    else:
        line("Offsets used", "none -- categories are unaffected; only the basis text would get richer")
    if result['warnings']:
        r_ += 1
        line("Warnings")
        for w in result['warnings']:
            line("  !", w)
    r_ += 1
    line("What this tool does NOT do")
    for t in ("No Lead Score / A-B-C-D tiers (needs the invited all-data sheet + payment feed)",
              "No cross-week persistence -- single session only",
              "No payment / conversion matching",
              "Phone may be blank when the Zoom export has no phone column",
              "~1-2% of chat is unattributable (same-name registrants) and is dropped, never guessed"):
        line("  -", t)

    wb.close()
    if wrfail:
        raise GateError("%d cell(s) failed to write completely -- investigate before publishing" % wrfail)
    return path
