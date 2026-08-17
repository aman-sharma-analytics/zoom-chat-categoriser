# -*- coding: utf-8 -*-
"""Session pipeline: room inputs -> categorised people.

Wires the ported modules together in the engine's order:
  parse_attendee -> parse_chat -> attribute (per room) -> merge people across rooms
  -> dimensions (D1/D3/D4/D5/D7 + message counts) -> categorize -> chat_cleaner split.

Row model (one row per unique person across all rooms):
  * identity: contact_norm.key_email > cleaned email > room-scoped name/phone key
  * chat: contact-attributed messages only; same-name leftovers appear marked, on the
    engine-winner's row, and NEVER feed categorisation (no_text cap -> 'no clear intent')
  * Attended=No registrants publish as 'non attended' and contribute nothing else
"""
import datetime
import hashlib
import io
import os
import re
import zipfile
from collections import Counter, OrderedDict

from . import chat_cleaner
from . import contact_norm as CN
from . import parse_attendee as PA
from . import parse_chat as PC
from .attribute import build_room_attribution
from .categorize import CAT_NA, CAT_NC, CAT_ORDER, categorize_person
from .dimensions import Scorer, load_config

DEFAULT_CFG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'config', 'phrase_banks.json')

# The engine's pricing/CTA beats are ~10-minute dense windows around the on-stage moment;
# a user-typed minute offset becomes [start+offset, start+offset+10min].
WINDOW_MIN = 10

COUNTRY_CC = {
    'India': '91', 'United Arab Emirates': '971', 'United States': '1', 'Saudi Arabia': '966',
    'Australia': '61', 'Germany': '49', 'Japan': '81', 'Singapore': '65', 'Netherlands': '31',
    'Pakistan': '92', 'Oman': '968', 'United Kingdom': '44', 'Canada': '1', 'Nigeria': '234',
    'Kuwait': '965', 'Qatar': '974', 'Kenya': '254', 'Nepal': '977', 'Bangladesh': '880',
    'France': '33', 'Romania': '40', 'Thailand': '66', 'Uganda': '256', 'Norway': '47',
    'Hong Kong SAR': '852', 'Hong Kong': '852', 'Italy': '39', 'Sri Lanka': '94', 'Malaysia': '60',
    'Philippines': '63', 'Bahrain': '973', 'New Zealand': '64', 'Ghana': '233', 'Uzbekistan': '998',
    'Ireland': '353', 'Brazil': '55', 'Denmark': '45', 'Bhutan': '975', 'Belgium': '32', 'Mexico': '52',
    'Switzerland': '41', 'Spain': '34', 'Sweden': '46', 'South Africa': '27', 'Indonesia': '62',
    'China': '86', 'Egypt': '20', 'Turkey': '90', 'Russia': '7', 'Vietnam': '84', 'Poland': '48',
    'Portugal': '351', 'Austria': '43', 'Finland': '358', 'Greece': '30', 'Israel': '972',
    'Mauritius': '230', 'Tanzania': '255', 'Zambia': '260', 'Zimbabwe': '263', 'Ethiopia': '251',
    'Morocco': '212', 'Jordan': '962', 'Lebanon': '961', 'Afghanistan': '93', 'South Korea': '82',
}
DEFAULT_CC = '91'   # workshop is India-centric; blank/unknown country defaults to +91


def cc_split(country, phone):
    """Split a normalised phone into ('+<dialcode>', '<national number>'). Engine port."""
    raw = str(phone if phone is not None else '').strip()
    d = re.sub(r'\D', '', raw)
    if not d:
        return ('', '')
    cc = COUNTRY_CC.get(str(country if country is not None else '').strip())
    if raw.startswith('+'):
        if cc and d.startswith(cc):
            return ('+' + cc, d[len(cc):])
        for k in sorted(set(COUNTRY_CC.values()), key=len, reverse=True):
            if d.startswith(k):
                return ('+' + k, d[len(k):])
        return ('', '+' + d)
    if cc and cc != DEFAULT_CC and len(d) > 10 and d.startswith(cc):
        return ('+' + cc, d[len(cc):])
    if len(d) == 12 and d.startswith(DEFAULT_CC):
        return ('+' + DEFAULT_CC, d[2:])
    if len(d) < 9:
        if cc and len(d) >= 7:
            return ('+' + cc, d)
        return ('', raw)
    return ('+' + (cc or DEFAULT_CC), d)


_ACRE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def all_chats_cell(msgs, unattr=None):
    """Verbatim engine port (_all_chats): full chat, one cell, ' | '-separated, 32k cap."""
    _pre = ""
    if not msgs:
        if not unattr:
            return ""
        msgs, _n = unattr
        if not msgs:
            return ""
        _pre = "[unattributed -- one of %d same-name registrants in this room] " % _n
    s = _pre + " | ".join(str(m).replace("\n", " ").replace("\r", " ").strip() for m in msgs)
    s = _ACRE.sub(" ", s)
    return (s[:32000] + " ...[truncated]") if len(s) > 32000 else s


# ---------------------------------------------------------------- input loading
def load_session(inputs):
    """inputs: list of (filename, bytes) uploads -- session/room zips and/or loose
    attendee CSVs + chat TXTs. Returns (rooms, notes):
      rooms: {room: {'attendee_texts': [...], 'chat_texts': [...], 'files': [names]}}
    A file is an attendee report iff it has an 'Attended'-headed section; a chat file
    iff the chat HEADER regex fires (content, never the filename). Identical payloads
    (nested duplicate zip copies) are deduped by hash."""
    rooms = {}
    notes = []
    seen = set()

    def add_file(room, name, data):
        h = hashlib.md5(data).hexdigest()
        if h in seen:
            notes.append("duplicate copy skipped: %s (%s)" % (name, room))
            return
        seen.add(h)
        low = name.lower()
        d = rooms.setdefault(room, {'attendee_texts': [], 'chat_texts': [], 'files': []})
        if low.endswith('.csv'):
            text = PA.decode_csv_bytes(data)
            if PA._find_attendee_header(text.splitlines()) is not None:
                d['attendee_texts'].append(text)
                d['files'].append(name)
            else:
                notes.append("%s (%s): CSV without an attendee section -- ignored (poll/registration export?)" % (name, room))
        elif low.endswith('.txt'):
            text = PC.decode_chat_bytes(data)
            if PC.looks_like_chat(text):
                d['chat_texts'].append(text)
                d['files'].append(name)
            else:
                notes.append("%s (%s): .txt without Zoom chat headers -- ignored" % (name, room))

    for fname, data in inputs:
        if fname.lower().endswith('.zip'):
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
            except Exception as e:
                notes.append("%s: cannot open zip (%s)" % (fname, e))
                continue
            for n in z.namelist():
                if n.endswith('/'):
                    continue
                if not n.lower().endswith(('.csv', '.txt')):
                    continue
                parts = n.split('/')
                room = parts[-2] if len(parts) >= 2 else 'root'
                try:
                    add_file(room, n, z.read(n))
                except Exception as e:
                    notes.append("%s: unreadable member %s (%s)" % (fname, n, e))
        else:
            add_file('root', fname, data)
    # drop rooms that ended up with nothing usable
    rooms = {r: d for r, d in rooms.items() if d['attendee_texts'] or d['chat_texts']}
    return rooms, notes


def load_session_from_pairs(att_inputs, chat_inputs):
    """Loose-file flow (no zip): att_inputs / chat_inputs are [(filename, bytes)] from the
    two upload blocks. Rooms are formed by PAIRING the lists in upload order:

      * 1 attendee report               -> one room, ALL chat files belong to it
      * N reports + N chat files        -> report #k + chat #k = Room k
      * N reports + 0 chat files        -> N chat-less rooms (everyone non-chatted)
      * N reports + M != N chat files   -> refused with a clear message -- chat can only
        be attributed INSIDE its own room, so a wrong pairing silently mis-attributes.

    Content is validated the same way as the zip flow: a CSV must contain an Attendee
    Details section; a .txt must contain Zoom chat headers. Byte-identical duplicates
    are dropped."""
    notes = []
    seen = set()

    def _dedup(pairs, kind):
        out = []
        for fname, data in pairs:
            h = hashlib.md5(data).hexdigest()
            if h in seen:
                notes.append("duplicate copy skipped: %s" % fname)
                continue
            seen.add(h)
            out.append((fname, data))
        return out

    att_inputs = _dedup(att_inputs, 'att')
    chat_inputs = _dedup(chat_inputs, 'chat')

    atts = []
    for fname, data in att_inputs:
        text = PA.decode_csv_bytes(data)
        if PA._find_attendee_header(text.splitlines()) is not None:
            atts.append((fname, text))
        else:
            notes.append("%s: no 'Attendee Details' section found -- this is not a Zoom "
                         "attendee report (poll/registration export?); ignored" % fname)
    chats = []
    for fname, data in chat_inputs:
        text = PC.decode_chat_bytes(data)
        if PC.looks_like_chat(text):
            chats.append((fname, text))
        else:
            notes.append("%s: no Zoom chat headers found -- not a saved-chat file; ignored" % fname)

    if not atts:
        return {}, notes
    rooms = {}
    if len(atts) == 1:
        room = 'Session'
        rooms[room] = {'attendee_texts': [atts[0][1]], 'chat_texts': [t for _f, t in chats],
                       'files': [atts[0][0]] + [f for f, _t in chats]}
    else:
        if chats and len(chats) != len(atts):
            raise ValueError(
                "%d attendee report(s) but %d chat file(s). With several rooms, chat can only be "
                "attributed inside its own room, so the tool needs exactly one chat file per "
                "attendee report (paired in upload order) -- or no chat files at all. Please "
                "re-order or complete the uploads." % (len(atts), len(chats)))
        for i, (fname, text) in enumerate(atts):
            room = 'Room %d' % (i + 1)
            d = {'attendee_texts': [text], 'chat_texts': [], 'files': [fname]}
            if chats:
                d['chat_texts'].append(chats[i][1])
                d['files'].append(chats[i][0])
                notes.append("%s: paired %r with %r" % (room, fname, chats[i][0]))
            rooms[room] = d
    return rooms, notes


# ---------------------------------------------------------------- per-room processing
def _merge_recs(rec_maps):
    """2+ attendee CSVs for one room: keep the RICHER record per key (engine rule)."""
    if len(rec_maps) == 1:
        return rec_maps[0]
    out = {}
    for rm in rec_maps:
        for k, v in rm.items():
            if k not in out or (v.get('minutes_present') or 0) > (out[k].get('minutes_present') or 0):
                out[k] = v
    return out


def _room_key(room, rec, pk):
    ke = CN.key_email(rec.get('email'))
    if ke:
        return ke
    if rec.get('email'):
        return 'em:' + rec['email']
    return pk + '#' + room          # name:/ph: keys stay room-scoped


class GateError(Exception):
    """A blocking quality-gate failure: the output must NOT be downloaded."""


def process_session(rooms, pricing_offset=None, cta_offset=None, cfg_path=DEFAULT_CFG,
                    progress=None):
    """rooms from load_session() -> result dict (rows, stats, warnings, audit...).

    pricing_offset / cta_offset: minutes from Actual Start Time, or None. Optional --
    they only enrich 'present at pricing' / 'stayed to CTA' and the Category Basis text;
    the engagement category itself never depends on them."""
    def tick(msg):
        if progress:
            progress(msg)

    scorer = Scorer(load_config(cfg_path))
    warnings = []
    room_stats = OrderedDict()
    S = Counter()
    session_dates = []

    people = OrderedDict()      # global_key -> person dict
    phone_owner = {}            # normalised phone -> key_email of its single chat owner

    for room in sorted(rooms):
        d = rooms[room]
        tick("parsing room %r" % room)
        rec_maps, metas, pstats = [], [], Counter()
        for text in d['attendee_texts']:
            recs_, meta_, st_ = PA.aggregate_text(text)
            rec_maps.append(recs_)
            metas.append(meta_)
            for k in ('nul', 'bad', 'cand', 'kept'):
                pstats[k] += st_.get(k) or 0
            pstats['has_phone_col'] |= bool(st_.get('has_phone_col'))
        recs = _merge_recs(rec_maps) if rec_maps else {}
        meta = next((m for m in metas if m.get('start')), metas[0] if metas else {})
        if meta.get('start'):
            session_dates.append(meta['start'].date())

        parsed = []
        for text in d['chat_texts']:
            bl, nh = PC.parse_chat_text(text)
            if nh == 0 and len(text.splitlines()) > 50:
                warnings.append("room %r: a chat-like file parsed 0 headers -- possible new Zoom format" % room)
            keys = {(b['sec'], PC.norm_name(b['sender']), b['lines'][0][:40] if b['lines'] else '')
                    for b in bl}
            parsed.append((bl, keys))
        # Zoom saves the SAME meeting's chat under two filenames/formats
        # (meeting_saved_chat.txt / meeting_saved_new_chat.txt). Byte-identical copies are
        # already deduped by hash; here we also drop a file whose messages are (almost)
        # entirely contained in another file's -- otherwise every message counts twice.
        parsed.sort(key=lambda t: -len(t[1]))
        blocks = []
        kept_keys = []
        for bl, keys in parsed:
            dup_of = None
            for kk in kept_keys:
                if keys and len(keys & kk) >= 0.9 * len(keys):
                    dup_of = kk
                    break
            if dup_of is not None:
                warnings.append("room %r: a chat file duplicates another one's messages (same meeting "
                                "saved in both Zoom formats?) -- the smaller copy was skipped" % room)
                continue
            kept_keys.append(keys)
            blocks.extend(bl)

        if pstats.get('nul'):
            warnings.append("room %r: %d attendee row(s) contained NUL bytes (repaired, kept) -- ask for a clean re-export"
                            % (room, pstats['nul']))
        if pstats.get('bad'):
            warnings.append("room %r: %d attendee row(s) were unparseable and skipped" % (room, pstats['bad']))
        if d['attendee_texts'] and not recs:
            warnings.append("room %r: attendee report parsed 0 rows (renamed header?) -- room contributes no people" % room)
        if not d['attendee_texts']:
            warnings.append("room %r: chat present but NO attendee report -- its chat cannot be attributed and is LOST" % room)
            room_stats[room] = {'people': 0, 'attended': 0, 'chat_blocks': len(blocks), 'lost': True}
            continue
        if not d['chat_texts'] and recs:
            warnings.append("room %r: attendee report present but NO chat export -- every attendee will read as non-chatted" % room)
        if recs and not meta.get('start'):
            warnings.append("room %r: 'Actual Start Time' did not parse -- pricing/CTA windows and same-name "
                            "chat disambiguation are degraded for this room" % room)

        tick("attributing chat in room %r" % room)
        attn = build_room_attribution(recs, meta, blocks)
        S.update(attn['stats'])

        # chat-span sanity (truncated exports look exactly like a quiet room)
        secs = [b['sec'] for b in blocks if b['sec'] is not None]
        span_min = (max(secs) - min(secs)) / 60.0 if len(secs) > 1 else 0.0
        dur = meta.get('duration_min') or 0
        if blocks and dur and span_min < 0.5 * dur:
            warnings.append("room %r: chat spans only %.0f min of a ~%.0f min session -- TRUNCATED chat export; "
                            "this room's attendees will falsely read as silent. Re-export before trusting it."
                            % (room, span_min, dur))

        n_att = sum(1 for r in recs.values() if r.get('attended_flag'))
        room_stats[room] = {'people': len(recs), 'attended': n_att,
                            'chat_blocks': len(blocks), 'chat_span_min': round(span_min),
                            'duration_min': dur, 'nul_rows': pstats.get('nul', 0),
                            'has_phone_col': bool(pstats.get('has_phone_col'))}

        # windows for this room (optional offsets; scalar minutes -> a 10-minute window,
        # a (lo, hi) minute pair -> that exact window)
        def _mkwin(off):
            if off is None or not meta.get('start'):
                return None
            if isinstance(off, (tuple, list)) and len(off) == 2:
                return (meta['start'] + datetime.timedelta(minutes=float(off[0])),
                        meta['start'] + datetime.timedelta(minutes=float(off[1])))
            lo = meta['start'] + datetime.timedelta(minutes=float(off))
            return (lo, lo + datetime.timedelta(minutes=WINDOW_MIN))
        win_p = _mkwin(pricing_offset)
        win_c = _mkwin(cta_offset)

        # ---- fold this room's people into the global map
        for pk, rec in recs.items():
            gk = _room_key(room, rec, pk)
            p = people.get(gk)
            if p is None:
                p = people[gk] = {
                    'key': gk, 'name': rec.get('name') or '', 'orig': rec.get('orig') or '',
                    'email': rec.get('email') or '', 'phone': rec.get('phone') or '',
                    'country': rec.get('country') or '', 'attended': False,
                    'minutes': 0.0, 'rooms': [], 'blocks': [], 'unattr': None,
                    'engine_chatted': False, 'pricing': None, 'cta': None,
                    'durations': [], 'no_timing': False,
                }
            if rec.get('name') and ((rec.get('minutes_present') or 0) >= p['minutes'] or not p['name']):
                p['name'] = rec['name']
            p['email'] = p['email'] or rec.get('email', '')
            p['phone'] = p['phone'] or rec.get('phone', '')
            p['country'] = p['country'] or rec.get('country', '')
            p['attended'] |= bool(rec.get('attended_flag'))
            p['minutes'] += rec.get('minutes_present') or 0
            p['rooms'].append((room, rec.get('minutes_present') or 0))
            if dur:
                p['durations'].append(dur)
            if (not (rec.get('seg_list') or rec.get('first_join'))
                    and (rec.get('attended_flag') or rec.get('unparsed_ts'))):
                p['no_timing'] = True
            segs = rec.get('seg_list') or []
            def _present(win):
                if not win or not segs:
                    return None
                ws, we = win
                return any(a <= we and b >= ws for a, b in segs if a and b)
            for fld, win in (('pricing', win_p), ('cta', win_c)):
                v = _present(win)
                if v is not None:
                    p[fld] = (p[fld] or False) or v

        # attach attributed chat + unattr pools + engine name-view (email-keyed people only)
        for ke, bl in attn['by_key'].items():
            p = people.get(ke)
            if p is not None:
                p['blocks'].extend(bl)
            else:
                S['msg_key_not_in_people'] += sum(len(b['lines']) for b in bl)
        for ke, ua in attn['unattr'].items():
            p = people.get(ke)
            if p is not None:
                prev = p['unattr']
                p['unattr'] = ((prev[0] if prev else []) + ua[0], max(ua[1], prev[1] if prev else 0))
        for nm in attn['name_chat']:
            pk = attn['winners'].get(nm)
            if pk is None:
                continue
            gk = _room_key(room, recs[pk], pk)
            if gk in people:
                people[gk]['engine_chatted'] = True
        for ph, ke in attn.get('by_phone', {}).items():
            cur = phone_owner.get(ph)
            if cur is None:
                phone_owner[ph] = ke
            elif cur != ke:
                phone_owner[ph] = False     # shared across identities -> unusable

    # ---- phone fallback (engine's _corpus_msgs order: email first, then phone): a row
    # whose email never keyed a message borrows the chat of its phone-sharing twin
    # registration -- for DISPLAY and categorisation text only, never for dims.
    for gk, p in people.items():
        if p['blocks'] or not p['attended'] or not p['phone']:
            continue
        owner_key = phone_owner.get(p['phone'])
        if owner_key and owner_key != gk and owner_key in people and people[owner_key]['blocks']:
            p['borrowed_from'] = owner_key
            S['phone_fallback_rows'] += 1

    # ---------------------------------------------------------------- categorise + split
    tick("scoring %d people" % len(people))
    rows = []
    catc = Counter()
    for gk, p in people.items():
        borrowed = p.get('borrowed_from')
        if borrowed:
            lines = [l for b in people[borrowed]['blocks'] for l in b['lines']]
            joined = []      # borrowed text never scores the borrower's own dims
        else:
            lines = [l for b in p['blocks'] for l in b['lines']]
            joined = [' '.join(b['lines']) for b in p['blocks']]
        has_cm = bool(lines)
        show_ua = (not has_cm) and p['engine_chatted'] and p['unattr']
        if not p['attended']:
            cat, basis = CAT_NA, "registered, never joined"
            dims = {}
        else:
            dims = scorer.score_messages(joined) if joined else {'D1': 0, 'D3': 0, 'D4': 0, 'D5': 0, 'D7': 0,
                                                                 'mean': 0, 'intent': 0, 'neg': 0}
            dur = max(p['durations']) if p['durations'] else 0
            pct = None
            if not p['no_timing'] or p['minutes'] > 0:
                pct = round(min(100.0, 100.0 * p['minutes'] / dur), 1) if dur else None
            orphan = has_cm and not p['engine_chatted']
            no_text = p['engine_chatted'] and not has_cm
            cat, basis = categorize_person(
                p['name'], p['engine_chatted'], pct,
                p['cta'], p['pricing'],
                dims, {'intent': dims.get('intent', 0), 'mean': dims.get('mean', 0), 'neg': dims.get('neg', 0)},
                lines, orphan=orphan, no_text=no_text)
            if orphan and cat != CAT_NC:
                basis = "contact-matched chat (engine room-match miss); " + basis
        ac = all_chats_cell(lines, p['unattr'] if show_ua else None)
        if ac:
            ccr = chat_cleaner.process_cell(ac)
            rel, dele = ccr["relevant"][:32000], ccr["deleted"][:32000]
        else:
            rel, dele = "", ""
        cc, pn = cc_split(p['country'], p['phone'])
        rooms_disp = " | ".join(r for r, _m in sorted(p['rooms'], key=lambda t: -t[1]))
        catc[cat] += 1
        rows.append({
            'name': p['name'], 'email': p['email'], 'cc': cc, 'phone': pn,
            'category': cat, 'basis': basis, 'relevant': rel, 'deleted': dele,
            'all_chats': ac, 'chatted': 'Yes' if (has_cm or show_ua) else 'No',
            'room': rooms_disp, 'attended': 'Yes' if p['attended'] else 'No',
            'minutes': round(p['minutes'], 1), 'msg_count': len(lines),
            'pricing': p['pricing'], 'cta': p['cta'],   # True / False / None (no offsets)
        })

    # deterministic, deliverable-friendly order: category priority, then presence
    _catrank = {c: i for i, c in enumerate(CAT_ORDER)}
    rows.sort(key=lambda r: (_catrank.get(r['category'], 99), -r['minutes'], r['name'].lower()))

    # ---------------------------------------------------------------- §7 invariants (hard)
    n_people = len(people)
    assert len(rows) == n_people, "row count %d != unique people %d" % (len(rows), n_people)
    allowed = set(CAT_ORDER)
    for r in rows:
        assert r['category'] in allowed, "unknown category %r" % r['category']
        if r['relevant'] or r['deleted']:
            assert r['all_chats'], "Relevant/Removed populated without chat: %r" % r['name']
            assert r['chatted'] == 'Yes'
        if r['category'] in (CAT_NC, CAT_NA):
            assert not r['relevant'] and not r['deleted'], \
                "chat text on a %r row: %r" % (r['category'], r['name'])

    # recurring anomaly audit -- all three counters must be ZERO or the build is blocked
    tick("running chat-clean audit")
    aud = chat_cleaner.audit_relevant([r['relevant'] for r in rows])
    audit_bad = {k: len(aud[k]) for k in ('bad_chars', 'repeats', 'vocab_leaks')}

    unresolved = S.get('msg_still_ambiguous', 0) + S.get('msg_no_csv_row', 0)
    attributed = S.get('msg_unique', 0) + S.get('msg_time_resolved', 0)
    result = {
        'rows': rows, 'category_counts': dict(catc), 'room_stats': dict(room_stats),
        'attribution': {
            'senders_unique': S.get('sender_unique', 0),
            'senders_window_resolved': S.get('sender_time_resolved', 0),
            'senders_unresolved': S.get('sender_unresolved', 0),
            'senders_no_csv_row': S.get('sender_no_csv_row', 0),
            'msgs_attributed': attributed,
            'msgs_dropped': unresolved,
            'msgs_dropped_pct': round(100.0 * unresolved / max(1, unresolved + attributed), 2),
            'phone_fallback_rows': S.get('phone_fallback_rows', 0),
        },
        'warnings': warnings,
        'audit': audit_bad,
        'audit_detail': {k: aud[k][:20] for k in ('bad_chars', 'repeats', 'vocab_leaks')},
        'freq_suspects': aud['freq_suspects'],
        'pricing_offset': pricing_offset, 'cta_offset': cta_offset,
        'session_date': min(session_dates).isoformat() if session_dates else None,
    }
    if any(audit_bad.values()):
        # junk leaked into Relevant Chat -- the deliverable must not ship
        result['gate_failed'] = ("chat-clean audit non-zero: bad_chars=%(bad_chars)d "
                                 "repeats=%(repeats)d vocab_leaks=%(vocab_leaks)d" % audit_bad)
    return result
