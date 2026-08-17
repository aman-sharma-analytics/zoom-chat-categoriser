# -*- coding: utf-8 -*-
"""Upload pre-flight -- check a workshop export BEFORE categorising it.

Port of the engine's preflight.py. Every defect it looks for has already shipped at
least once, and each one LOOKED like a scoring problem at the time:

  * 12 Jul 'Ops 6'  -- NUL bytes in the attendee CSV cost a whole room (~4,800 people).
  * 2 Aug 'Zoom 11' -- an 11 KB chat export for a 3h45m session: 1.4% of the room
                       carried chat vs 30-50% elsewhere; ~2,750 people published silent.
  * 21 Jun 'Evening'-- ships only a poll CSV, no attendee report: room lost.
  * 19 Jul OPS 3/5  -- no chat export at all.

A FAIL means the room will be silently degraded or lost if you score the export as-is.
Processing is still allowed (a no-chat room legitimately publishes as non-chatted), but
the user must see the verdicts first.
"""
import re

from . import parse_attendee as PA

MIN_CHAT_SPAN_FRAC = 0.50    # chat should span at least half the session
MIN_TS_PARSE_FRAC = 0.75     # at least 75% of join timestamps must parse
MAX_NO_SHARE = 0.60          # >60% Attended=No is a registration export, not an attendee report

_TS = re.compile(r'(\d{2}):(\d{2}):(\d{2})')


def check_rooms(rooms):
    """rooms: pipeline.load_session output. Returns list of per-room verdicts:
    {'room', 'level': 'ok'|'WARN'|'FAIL', 'messages': [...], 'attended': n}."""
    out = []
    for room in sorted(rooms):
        d = rooms[room]
        msgs = []
        fail = warn = False
        yes = no = 0

        if not d['attendee_texts']:
            msgs.append("no attendee report in this room's files (poll/registration export only?)")
            fail = True
        for text in d['attendee_texts']:
            nul = text.count('\x00')
            if nul:
                msgs.append("%d NUL byte(s) in the attendee CSV -- repaired here, but ask for a clean re-export" % nul)
                warn = True
            lines = text.splitlines()
            st = {}
            rows = PA.parse_attendee_rows(lines, st)
            if not rows:
                msgs.append("attendee report parsed 0 rows (renamed header?)")
                fail = True
                continue
            joins = []
            for r in rows:
                if r.get('Attended') == 'Yes':
                    yes += 1
                    joins.append(r.get('Join Time'))
                else:
                    no += 1
            share = no / float(yes + no) if (yes + no) else 0.0
            if share > MAX_NO_SHARE:
                msgs.append("%.0f%% of rows are Attended=No -- registration export, not an attendee report" % (100 * share))
                fail = True
            if joins:
                ok = sum(1 for j in joins if PA.parse_dt(j))
                fr = ok / float(len(joins))
                if fr < MIN_TS_PARSE_FRAC:
                    msgs.append("only %.0f%% of join timestamps parse -- timing will read as 0 minutes" % (100 * fr))
                    fail = True
            meta = PA.session_meta(lines)
            dur = meta.get('duration_min') or 200.0

        if not d['chat_texts']:
            msgs.append("NO chat export -- every attendee in this room will read as non-chatted")
            fail = True
        else:
            dur = dur if d['attendee_texts'] else 200.0
            for text in d['chat_texts']:
                ts = _TS.findall(text)
                if len(ts) < 2:
                    msgs.append("chat export has no usable timestamps")
                    fail = True
                    continue
                secs = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in ts]
                span = (max(secs) - min(secs)) / 60.0
                if span < dur * MIN_CHAT_SPAN_FRAC:
                    msgs.append("chat spans only %.0f min of a ~%.0f min session (%d attended rows) -- TRUNCATED EXPORT"
                                % (span, dur, yes))
                    fail = True

        out.append({'room': room, 'attended': yes,
                    'level': 'FAIL' if fail else ('WARN' if warn else 'ok'),
                    'messages': msgs})
    return out
