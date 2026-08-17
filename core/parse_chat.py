# -*- coding: utf-8 -*-
"""Zoom chat-export parsing (both wild formats).

Ported from the be10x lead-scoring engine (run.py / invited_report.py). Line format:

    HH:MM:SS From <Sender Name> to <Recipient>:
    YYYY-MM-DD HH:MM:SS From <Sender Name> to <Recipient>:

followed by one or more body lines. Rules that each cost real data:

  * REPLY_QUOTE lines quote the OTHER person's words -- never credit the replier/reactor.
  * The same room's chat ships under different filenames across Zoom versions
    (meeting_saved_chat.txt / meeting_saved_new_chat.txt) -> match on CONTENT (a file is
    a chat file iff the HEADER regex fires), and dedup identical payloads by hash.
  * New-format date prefixes anchor cross-midnight times (+24h/day); the old format has
    no date, so a >12h backwards jump in an otherwise chronological file means the clock
    wrapped midnight -> +24h.
  * BOM sniffing: UTF-16 exports exist; a BOM eaten as text silently loses the first header.
  * Hosts / bots (be10x accounts, notetaker bots) are excluded from person chat.
"""
import re
import unicodedata

HEADER = re.compile(r'^(?:\d{4}-\d{2}-\d{2}\s+)?(\d{1,2}:\d{2}:\d{2})\s+From\s+(.+?)\s+to\s+(.+?):\s*$')
REPLY_QUOTE = re.compile(r'^\s*(?:Replying to |Reacted to |Removed a )"?')

HOST_TOKENS = {'aditya goenka', 'aayush kachave', 'kachave', 'diptanil', 'be10x', 'team be10x',
               'be10x team', 'be10x support', 'be10x official', 'host', 'moderator', 'panelist', 'panelists'}
TEAM_RE = re.compile(r'\bbe ?10x\b|team be10x|fireflies|notetaker|otter\.ai|read\.ai|\bfathom\b|'
                     r'^\s*(co[- ]?host|host|moderator|panelists?|organiser|organizer|admin|team)\s*$', re.I)


def norm_name(raw):
    """BYTE-IDENTICAL to the engine's norm_name (and parse_attendee.norm_name)."""
    s = unicodedata.normalize('NFKC', str(raw)).strip().lower()
    s = re.sub(r'[^\w\s]', ' ', s, flags=re.UNICODE)
    return re.sub(r'\s+', ' ', s).strip()


def is_host_sender(raw):
    nm = norm_name(raw)
    return (nm in HOST_TOKENS) or bool(TEAM_RE.search(str(raw or '')))


def decode_chat_bytes(data):
    """Bytes -> text, BOM-aware (UTF-16 exports exist; utf-8-sig strips a UTF-8 BOM)."""
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return data.decode('utf-16', errors='replace')
    return data.decode('utf-8-sig', errors='replace')


def _secofday(tstr):
    try:
        h, m, s = str(tstr).split(':')
        return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        return None


def parse_chat_text(text):
    """Chat text -> (blocks, n_headers).

    Each block: {'sender': raw display name, 'ts': 'HH:MM:SS' as exported,
                 'sec': wrap-adjusted seconds since the file's day 0 (may exceed 86400),
                 'lines': [body line, ...]}  (reply/reaction quote lines already dropped;
                 empty bodies dropped; host/bot senders NOT filtered here -- attribution
                 owns that, so the caller can still count host traffic if it wants).

    Cross-midnight: date-prefixed headers anchor by real date delta (+24h/day, engine
    logic). Undated headers use monotonicity -- a >12h backwards jump wraps (+24h)."""
    blocks = []
    n_head = 0
    first_date = None      # date prefix of the first dated header
    day_off = 0            # whole days to add (undated wrap heuristic)
    prev_sec = None
    cur = None

    def flush(cur):
        if cur is None:
            return
        lines = [l for l in cur['lines'] if l]
        if not lines:
            return
        cur['lines'] = lines
        blocks.append(cur)

    for line in text.splitlines():
        m = HEADER.match(line.rstrip('\n'))
        if m:
            flush(cur)
            n_head += 1
            ts = m.group(1)
            sec = _secofday(ts)
            dp = line[:10] if (len(line) >= 11 and line[:4].isdigit() and line[4:5] == '-') else None
            if dp is not None and sec is not None:
                # new format: anchor to the first header's date
                if first_date is None:
                    first_date = dp
                elif dp != first_date:
                    try:
                        import datetime as _dt
                        nd = (_dt.date.fromisoformat(dp) - _dt.date.fromisoformat(first_date)).days
                        if nd > 0:
                            sec += 24 * 3600 * nd
                    except Exception:
                        pass
            elif sec is not None:
                # old format: wrap on a large backwards jump
                sec += day_off * 24 * 3600
                if prev_sec is not None and sec < prev_sec - 12 * 3600:
                    day_off += 1
                    sec += 24 * 3600
            if sec is not None:
                prev_sec = sec
            cur = {'sender': m.group(2).strip(), 'ts': ts, 'sec': sec, 'lines': []}
        elif cur is not None:
            if not REPLY_QUOTE.match(line):
                cur['lines'].append(line.strip('\t').strip())
    flush(cur)
    return blocks, n_head


def looks_like_chat(text):
    """Content-based chat detection (never trust the filename)."""
    for i, line in enumerate(text.splitlines()):
        if HEADER.match(line.rstrip('\n')):
            return True
        if i > 400:            # a real export has a header within the first few lines
            break
    return False
