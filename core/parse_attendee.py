# -*- coding: utf-8 -*-
"""Zoom attendee-report parsing (multi-section, NUL-tolerant).

Ported from the be10x lead-scoring engine's identity.py -- the production-validated
logic. Every guard here encodes a real incident:

  * NUL bytes: csv.reader raises on a line containing \\x00 and aborts the whole file.
    12 Jul 'Ops 6' carried 48 of them and lost ~4,800 people. One bad byte must cost
    one row, not a room -> per-row parse with strip-NUL retry.
  * One CSV holds THREE header sections (Host / Panelist / Attendee Details) with
    DIFFERENT layouts; only the Attendee Details section is parsed, with its own
    column indices.
  * 'Attended' = No rows are registrants who never joined: kept (they publish as
    'non attended') but they must never contribute segments, chat candidacy or rooms.
  * Zoom logs OVERLAPPING join segments -> minutes are a merged interval union,
    never a raw sum.
"""
import csv
import datetime
import re
import unicodedata
from collections import defaultdict

from . import contact_norm as CN

# ---------------------------------------------------------------- row-level CSV repair


def _csv_row(raw, _stats=None):
    """Parse ONE csv line, surviving the NUL bytes Zoom occasionally emits.

    The engine's original guard caught csv.Error('line contains NUL') -- but Python 3.11+
    no longer raises on NUL, it silently passes \\x00 THROUGH into field values instead.
    So: strip-and-count NULs up front (works on every Python, keeps values clean), and
    keep the try/except for any other per-line damage. One bad byte costs one row at
    worst, never a room. Returns the parsed row, or None if the line is unsalvageable."""
    if '\x00' in raw:
        raw = raw.replace('\x00', '')
        if _stats is not None:
            _stats['nul'] = _stats.get('nul', 0) + 1
    try:
        return next(csv.reader([raw]))
    except Exception:
        if _stats is not None:
            _stats['bad'] = _stats.get('bad', 0) + 1
        return None


# ---------------------------------------------------------------- text/format helpers
_INVIS = re.compile("[​-‏‪-‮⁠﻿ ]")
_REGID = re.compile(r"^\s*\d{6,}_+")          # leading registration-id prefix e.g. "2505445126_"
_WS = re.compile(r"\s+")


def norm_name(raw):
    """BYTE-IDENTICAL to the engine's norm_name: NFKC + keep letters of ANY script."""
    s = unicodedata.normalize('NFKC', str(raw)).strip().lower()
    s = re.sub(r'[^\w\s]', ' ', s, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', s).strip()


def clean_display_name(raw):
    s = _INVIS.sub('', str(raw or ''))
    s = _REGID.sub('', s)
    s = _WS.sub(' ', s).strip()
    return s.title() if s and s.upper() != s.lower() else s


def clean_email(raw):
    e = _INVIS.sub('', str(raw or '')).strip().lower().rstrip('.,;:')
    e = _WS.sub('', e)
    return e if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", e) else ''


def parse_dt(s):
    """Parse Zoom timestamps. Ordered most-specific-first; Zoom exports month-first."""
    s = str(s or '').strip().strip('"')
    if not s or s in ('--', '-'):
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M",
                "%m-%d-%Y %I:%M:%S %p", "%m-%d-%Y %I:%M %p", "%m-%d-%Y %H:%M:%S", "%m-%d-%Y %H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- CSV section parsing
ATT_HDR_START = "Attended,User Name (Original Name),First Name"
_SECTION_MARKERS = ("Host Details", "Panelist Details", "Attendee Details")


def session_meta(lines):
    """Pull Topic / Actual Start Time / Duration / registrant counts from the meta block."""
    meta = {}
    for i, l in enumerate(lines):
        if l.startswith("Topic,Webinar ID,Actual Start Time"):
            hdr = _csv_row(l) or []
            if i + 1 < len(lines) and hdr:
                row = _csv_row(lines[i + 1]) or []
                d = dict(zip(hdr, row))
                meta['topic'] = d.get('Topic', '').strip()
                meta['start'] = parse_dt(d.get('Actual Start Time', ''))
                try:
                    meta['duration_min'] = float(d.get('Actual Duration (minutes)', '') or 0)
                except ValueError:
                    meta['duration_min'] = 0.0
                try:
                    meta['registrants'] = int(d.get('# Registrants', '') or 0)
                except ValueError:
                    meta['registrants'] = 0
            break
    return meta


NULCH = chr(0)      # a literal NUL, spelled without an escape


def _find_attendee_header(lines):
    """Index of the Attendee Details header row.

    Primary: the engine's exact rule (startswith 'Attended,User Name (Original Name),First Name').
    Fallback: scan every row whose first cell is 'Attended' and pick the one whose section is
    attendee-shaped (has 'User Name (Original Name)' + 'Email' + one of First Name / Phone /
    Registration Time) -- the Host/Panelist sections lack those; if several match, take the LAST
    (Attendee Details is the final section in every real export)."""
    # Header DISCOVERY has to be NUL-tolerant as well. _csv_row repairs NULs, but it only
    # runs once a line has been recognised: a single NUL inside 'Attended,' (Zoom does emit
    # them -- 12 July / Ops 6 carries 48) failed both startswith tests, the header was never
    # found, and the whole room's attendees were discarded as 'parsed 0 rows'.
    def _bare(l):
        return l.replace(NULCH, '') if NULCH in l else l
    hi = next((i for i, l in enumerate(lines) if _bare(l).startswith(ATT_HDR_START)), None)
    if hi is not None:
        return hi
    cands = []
    for i, l in enumerate(lines):
        if not _bare(l).startswith("Attended,"):
            continue
        row = _csv_row(l) or []
        if not row or row[0].strip() != "Attended":
            continue
        if ("User Name (Original Name)" in row and "Email" in row
                and any(c in row for c in ("First Name", "Phone", "Registration Time"))):
            cands.append(i)
    return cands[-1] if cands else None


def parse_attendee_rows(lines, stats=None):
    """List of raw attendee dicts from the 'Attendee Details' section, NUL-tolerant per row.
    stats (optional dict) collects: nul (rows repaired), bad (rows skipped), cand/kept counts."""
    _st = stats if stats is not None else {}
    hi = _find_attendee_header(lines)
    if hi is None:
        return []
    hdr = _csv_row(lines[hi], _st)
    if not hdr:
        _st['header_unparseable'] = True
        return []
    out = []
    _cand = 0
    for raw in lines[hi + 1:]:
        if not raw.strip():
            continue
        # STOP at a new non-attendee section header (parsing on would misalign columns)
        if raw.split(',', 1)[0] in ("Host Details", "Panelist Details") or raw.startswith("Report generated"):
            break
        _cand += 1
        row = _csv_row(raw, _st)
        if row is None:
            continue
        if not row or row[0] not in ("Yes", "No"):
            continue
        out.append({hdr[j]: (row[j] if j < len(row) else '') for j in range(len(hdr))})
    _st['cand'] = _cand
    _st['kept'] = len(out)
    _st['has_phone_col'] = 'Phone' in hdr
    return out


# ---------------------------------------------------------------- interval merge (the quirk fix)
def merged_minutes(intervals):
    """intervals: list of (start_dt, end_dt). Union minutes (Zoom logs overlapping segments)."""
    iv = sorted([(a, b) for a, b in intervals if a and b and b >= a])
    if not iv:
        return 0.0
    total = datetime.timedelta()
    cs, ce = iv[0]
    for a, b in iv[1:]:
        if a <= ce:
            ce = max(ce, b)
        else:
            total += ce - cs
            cs, ce = a, b
    total += ce - cs
    return round(total.total_seconds() / 60.0, 1)


# ---------------------------------------------------------------- aggregate per person
def aggregate_text(text):
    """One room's attendee CSV text -> ({key: person_record}, meta, stats).

    Key ladder: email > 'name:'+norm > 'ph:'+phone (a symbol-only/blank-norm person must
    not merge everyone into one record). Segments come ONLY from parseable Join+Leave
    pairs; Attended=No rows carry '--' and contribute none."""
    lines = text.splitlines()
    stats = {}
    rows = parse_attendee_rows(lines, stats)
    meta = session_meta(lines)
    by = defaultdict(lambda: {'segments': [], 'phone': '', 'first': '', 'last': '',
                              'orig': '', 'reg_time': None, 'country': ''})
    for r in rows:
        email = clean_email(r.get('Email'))
        name = clean_display_name(r.get('User Name (Original Name)'))
        _nm = norm_name(name)
        _ph = CN.norm_phone(r.get('Phone'))
        key = email or (('name:' + _nm) if _nm else (('ph:' + _ph) if _ph else None))
        if key is None:
            continue
        p = by[key]
        p['email'] = email
        p['name'] = name or p.get('name', '')
        p['orig'] = r.get('User Name (Original Name)', '') or p['orig']
        if not p['phone']:
            p['phone'] = CN.norm_phone(r.get('Phone'))
        if not p.get('phone_raw'):
            p['phone_raw'] = str(r.get('Phone') or '').strip()
        p['first'] = (r.get('First Name') or p['first']).strip()
        p['last'] = (r.get('Last Name') or p['last']).strip()
        p['country'] = (r.get('Country/Region Name') or p.get('country', '') or '').strip()
        j, lv = parse_dt(r.get('Join Time')), parse_dt(r.get('Leave Time'))
        # `lv >= j` matters: Zoom exports rows whose Leave precedes its Join (its own
        # 'Time in Session (minutes)' goes NEGATIVE on those -- e.g. -178). Such a segment
        # says nothing about when the person was present. merged_minutes has always
        # discarded them, so keeping them in segments/name_segs made the three views
        # disagree: minutes read 0 while seg_list looked populated, which made
        # pct_attended publish a confident 0% and made the chat presence-window test rule
        # that candidate out instead of admitting it could not place them.
        if j and lv and lv >= j:
            p['segments'].append((j, lv))
        # Zoom's own attendance verdict: '--' join/leave rows are Attended=No registrants.
        attended_row = str(r.get('Attended', '')).strip().lower() == 'yes'
        if attended_row:
            p['attended_flag'] = True
            # people RENAME mid-session; each segment row carries the name in force at the
            # time. Keep every raw name variant with ITS OWN segments -- chat attribution
            # must be able to match any of them (engine rule: candidates are registered
            # per row, not per person).
            raw_nm = r.get('User Name (Original Name)', '') or ''
            if raw_nm:
                p.setdefault('name_segs', {}).setdefault(raw_nm, []).append(
                    (j, lv) if (j and lv and lv >= j) else None)
        for _f in ('Join Time', 'Leave Time'):
            _v = str(r.get(_f) or '').strip()
            if _v and _v not in ('--', '-') and parse_dt(_v) is None:
                p['unparsed_ts'] = True
        rt = parse_dt(r.get('Registration Time'))
        if rt and not p['reg_time']:
            p['reg_time'] = rt
    out = {}
    for key, p in by.items():
        segs = p['segments']
        joins = sorted([a for a, _ in segs])
        leaves = sorted([b for _, b in segs])
        out[key] = {
            'email': p.get('email', ''), 'phone': p['phone'], 'phone_raw': p.get('phone_raw', ''),
            'name': p['name'], 'norm': norm_name(p['name']), 'orig': p['orig'],
            'name_segs': p.get('name_segs', {}),
            'minutes_present': merged_minutes(segs),
            'seg_list': segs,
            'reconnections': max(0, len(segs) - 1),
            'first_join': joins[0] if joins else None,
            'last_leave': leaves[-1] if leaves else None,
            'attended_flag': bool(p.get('attended_flag')),
            'unparsed_ts': bool(p.get('unparsed_ts')),
            'pre_registered': p['reg_time'] is not None,
            'country': p.get('country', ''),
        }
    return out, meta, stats


def decode_csv_bytes(data):
    """Bytes -> text for an attendee CSV (BOM-aware; NULs survive for row-level repair)."""
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    return data.decode('utf-8-sig', errors='replace')


def pct_attended(rec, meta):
    """Minutes present as % of the session's actual duration (None when unknowable).
    Unknown timing (attended but no parseable stamp) must NOT read as 0% -- that is the
    'scored the worst timing for people we have no timing for' incident."""
    dur = meta.get('duration_min') or 0
    mp = rec.get('minutes_present', 0) or 0
    # A segment whose leave precedes its join carries no usable timing -- Zoom exports
    # these with a NEGATIVE 'Time in Session' of its own (measured: 107 people across the
    # 11 archived sessions have no other kind of segment). merged_minutes already drops
    # them, so without this they arrive here as a confident 0 minutes / 0%, which is the
    # exact "scored the worst timing for people we have no timing for" failure this
    # function exists to prevent.
    _segs = rec.get('seg_list') or []
    _usable = any(a and b and b >= a for a, b in _segs)
    no_timing = ((not (_segs or rec.get('first_join'))
                  or (_segs and not _usable))
                 and (rec.get('attended_flag') or rec.get('unparsed_ts')))
    if no_timing:
        return None
    return round(min(100.0, 100.0 * mp / dur), 1) if dur else None
