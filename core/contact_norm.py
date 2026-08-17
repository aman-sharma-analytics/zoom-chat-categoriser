#!/usr/bin/env python3
"""Shared email/phone canonicalisation for the lead-scoring engine + invited report.
Added 2026-07-05 (match-accuracy fix, user-approved). Imported by run.py and invited_report.py.
Design: EXACT keys unchanged and always win; these helpers only repair mechanical damage
(typo domains, doubled dial prefixes, junk placeholders) and provide guarded FALLBACK keys.
Self-test: python3 contact_norm.py
"""
import re, unicodedata

_DOM = {'gamil':'gmail','gmial':'gmail','gmal':'gmail','gmil':'gmail','gmaii':'gmail','gmali':'gmail',
        'gemail':'gmail','googlemail':'gmail','gmai':'gmail',
        'hotmial':'hotmail','hotmali':'hotmail','hotmil':'hotmail',
        'yaho':'yahoo','yahooo':'yahoo','yhaoo':'yahoo','rediffmal':'rediffmail'}
_TLD_FIX = {'con','vom','cim','comm','coom','om','cm','ocm','clm','xom'}   # applied ONLY to known providers
_PROV = {'gmail','yahoo','hotmail','outlook','rediffmail','icloud','live','aol','protonmail','ymail'}
_PLACE = {'1234567890','0123456789','9876543210','0987654321'}
# 2026-08-05 (bug review H10): _FREEMAIL was incomplete and DISAGREED with _PROV -- 'protonmail'
# is in _PROV (so canon_email repairs its typo domains) but 'protonmail.com' was absent here, so
# 'abc@protonmail.com' was not junk and became a live SHARED match key merging strangers into one
# identity. Same for rediff.com, the regional Yahoo/Outlook/Live TLDs and msn.com. The assertion
# below keeps the two sets from drifting apart again.
_FREEMAIL = {'gmail.com','googlemail.com','yahoo.com','yahoo.in','yahoo.co.in','yahoo.co.uk','yahoo.com.au',
             'hotmail.com','hotmail.co.uk','hotmail.in','outlook.com','outlook.in','rediffmail.com','rediff.com',
             'icloud.com','me.com','live.com','live.in','aol.com','ymail.com','protonmail.com','proton.me',
             'msn.com','zoho.com','zohomail.in','gmx.com','mail.com','yandex.com'}
_GENERIC_LOCAL = {'abc','abcd','abcde','xyz','test','testing','tester','na','no','none','nil','noemail','nomail','notavailable','nothing','asdf','asdfg','qwerty','dummy','sample','example','user','admin','email','mail','aaa','aaaa','fake','random','unknown',
                  # 2026-08-05 (H10): common placeholders this population actually types
                  'xxx','xxxx','demo','temp','tempmail','info','contact','support','me','my','myemail',
                  '123','1234','12345','123456','abc123','a1','nomailid','noid','not','nope','anonymous'}
_DISPOSABLE = {'mailinator.com','yopmail.com','tempmail.com','temp-mail.org','10minutemail.com','guerrillamail.com','trashmail.com','fakeinbox.com','sharklasers.com','getnada.com','dispostable.com','maildrop.cc','emailondeck.com','mohmal.com','mytemp.email'}
# 2026-07-29 fix (bug review L4): added U+2060 WORD JOINER. identity._INVIS stripped it while this set
# did not, so canon_email kept 'rahul<U+2060>k@gmail.com' as a key permanently split from
# 'rahulk@gmail.com' while identity.clean_email folded them together -- two identities for one row.
_INVIS = dict.fromkeys(map(ord, '​‌‍‎‏‪‫‬‭‮⁠﻿ '), None)

def digits(s):
    # 2026-07-29 fix (bug review L1): the E-notation junk guard below is STRING-only, but the float branch
    # converted to int FIRST, so a numeric cell that had already lost precision (918149000000.0 -- note the
    # trailing zeros where real digits used to be) sailed past it and entered the Indian bare-10 keyspace as
    # a confident-looking fake key. A genuine 12-digit Indian number (91 + 10) never ends in 5+ zeros, so
    # treat that shape as unrecoverable, exactly like '9.18149E+11'.
    # 2026-08-05 (bug review H12): the trailing-00000 guard below used to fire on int cells too.
    # openpyxl returns the invited sheet's phone column as int, and an int has lost NOTHING -- the
    # guard's own justification is float precision loss. It was refusing 14 real numbers in the
    # 2 Aug sheet alone (917096300000, 918496800000, 919037700000, ...): vanity/series numbers that
    # legitimately end in five zeros. The guard now applies only where precision CAN have been lost.
    _float_src = isinstance(s, float) and not isinstance(s, bool)
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        _t = repr(s)
        if 'e' in _t.lower(): return ''            # float too large/small to have kept its digits
        if _float_src and not s.is_integer():
            # 2026-07-29 (bug review L2): a non-integer float concatenated its fraction onto the number
            # ('919845344750.4' -> '98453447504'). Nothing here is recoverable.
            return ''
        _d = re.sub(r'\D', '', _t.split('.')[0])
        if _float_src and len(_d) >= 11 and _d.endswith('00000'): return ''
        s = int(s)
    s = str(s or '')
    if re.fullmatch(r'\s*\d+(?:\.\d+)?[eE][+-]?\d+\s*', s):
        # 2026-07-26 fix (bug review #3): Excel re-saves render long phones as '9.18149E+11'. Only ~6
        # significant digits survive, so the true number is unrecoverable and the digit-strip of the
        # mantissa ('91814911') was a FAKE key that collided strangers. Treat as junk.
        return ''
    # 2026-08-05 (bug review H8): the two mangle guards above were reachable ONLY from the numeric
    # branch, but every CSV reader in this pipeline (run.py, invited_report.py -- csv.reader /
    # DictReader) yields STRINGS, so the string path is the dominant one and it bypassed both.
    # digits('919845344750.4') still returned '9198453447504' -> norm_phone '98453447504', which is
    # verbatim the defect the L2 comment above claims to have fixed. Worse, the SAME source value
    # keyed two different ways depending on whether it arrived via CSV or XLSX -- a silent
    # false-merge / false-split generator. Both guards now apply to the string spelling as well.
    m = re.fullmatch(r'\s*(\d+)\.0+\s*', s)
    if m:
        _d = m.group(1)
        if len(_d) >= 11 and _d.endswith('00000'): return ''   # float-mangled, as '918149000000.0'
        return _d
    if re.fullmatch(r'\s*\d+\.\d*[1-9]\d*\s*', s):
        return ''      # a fractional phone is a mangled float, never a real number
    return re.sub(r'\D', '', s)

def canon_email(e):
    """Lower/NFKC/invisible-char strip + inner-space removal + trailing punctuation
    + known-provider typo-domain repair (gamil->gmail, gmail.con->gmail.com). '' if not email-shaped."""
    e = unicodedata.normalize('NFKC', str(e or '')).translate(_INVIS).strip().lower()
    e = e.replace(' ', '').rstrip('.,;:')
    if '@' not in e: return ''
    loc, _, dom = e.rpartition('@')
    if not loc or not dom: return ''
    parts = dom.split('.')
    if parts[0] in _DOM: parts[0] = _DOM[parts[0]]
    if len(parts) >= 2 and parts[0] in _PROV and parts[-1] in _TLD_FIX: parts[-1] = 'com'
    _dom2 = '.'.join(parts)
    if '.' not in _dom2: return ''   # 2026-07-26 Phase-4 (bug review #37): TLD-less 'abc@gmail' must not become a shared match key
    return loc + '@' + _dom2

def gmail_key(e):
    """Dot/+tag-insensitive key for gmail addresses (fallback matching only). None otherwise."""
    if not e: return None
    loc, _, dom = str(e).rpartition('@')
    if dom != 'gmail.com': return None
    loc = loc.split('+', 1)[0].replace('.', '')
    return (loc + '@gmail.com') if loc else None

def key_email(e):
    """Canonical MATCH KEY for an email: canon first, junk-filtered, gmail dot/+tag folded.
    Returns '' when not usable as a key. (2026-07-26 Phase-3: canon-before-key, everywhere.)"""
    ce = canon_email(e)
    if not ce or is_junk_email(ce): return ''
    k = gmail_key(ce) or ce
    # 2026-07-29 fix (bug review H4): the junk test ran on the UNFOLDED address, so dot/+tag folding could
    # LAND on a blacklisted key -- key_email('a.b.c@gmail.com') returned 'abc@gmail.com', the very key
    # is_junk_email exists to block ("two different people both abc@gmail.com"). Verified for
    # a.b.c / t.e.s.t+x / x.y / a.d.m.i.n. Re-test the folded form and refuse it too.
    if k != ce and is_junk_email(k): return ''
    return k

def is_junk_phone(d):
    """Placeholder/junk digit strings: <=2 distinct digits or known sequences (incl. after a dial code)."""
    d = str(d or '')
    if not d: return False
    # 2026-08-05 (bug review M25): "<=2 distinct digits" also discarded legitimate VANITY numbers,
    # which are common in this market -- the live payment report contains a Rs 16,014 payment on
    # '+918080000888' (digits {8,0}) whose phone key was thrown away, so it could only ever match
    # by email. A placeholder is a long RUN of one digit or a known sequence, not merely a number
    # built from two digits. Require a run of 6+ identical digits, or an explicit placeholder.
    # 7+ identical digits in a row, not 6: measured against the live invited sheet, a 6-run
    # rejected 51 real numbers ('918144444431') to gain 22 -- a net loss. 7 keeps genuine vanity
    # numbers while still catching padded placeholders like '9800000000'.
    if re.search(r'(\d)\1{6,}', d): return True
    return len(set(d)) <= 1 or d in _PLACE or (len(d) >= 10 and d[-10:] in _PLACE)

def is_junk_email(e):
    """Emails that must NOT be used as match keys: generic locals on free providers
    (two different people both 'abc@gmail.com'), tiny locals, disposable domains.
    Business domains are exempt (info@company.com is a real contact).
    2026-07-26 Phase-3 (bug review #20): judges the CANON form -- 'abc@gamil.com' used to pass here,
    then canonicalise to abc@gmail.com at the call site and become a live shared match key."""
    e = str(e or '').strip().lower()
    e = canon_email(e) or e
    if '@' not in e: return False
    loc, _, dom = e.rpartition('@')
    if dom in _DISPOSABLE: return True
    if dom in _FREEMAIL and (loc in _GENERIC_LOCAL or len(loc) <= 2): return True
    return False

def peel(d):
    """Peel international-dialling junk aimed at Indian numbers: one leading 00, then
    repeated 91/0 prefixes while >10 digits remain (fixes doubled '9191...' entries).
    Foreign codes (971/44/880/1/...) are never touched: loop only strips 91/0."""
    if d.startswith('00'):
        # 2026-07-26 fix (bug review #4): 00 = international dialling. >12 digits = 00+91+number (Indian,
        # strip and continue). 11-12 digits = 00 + short foreign cc+number (e.g. Singapore 006582399770):
        # KEEP the 00 -- the zero-stripping loop below used to eat it one digit at a time and land the
        # foreign number in the INDIAN bare-10 keyspace.
        if len(d) > 12: d = d[2:]
        else: return d
    # 2026-08-05 (bug review H9 + M26). Two fixes to this loop:
    #  H9 -- it stripped a leading '0' from ANY number, so a foreign NATIONAL format ('07400232640',
    #        UK) became '7400232640', which passes every bare10 guard and enters the INDIAN keyspace
    #        where it can collide with a real Indian mobile. The docstring above claims foreign codes
    #        are never touched; the 0-strip is exactly what broke them. A trunk 0 is only peeled when
    #        what remains is a plausible Indian number (10 digits starting 6-9, or 91 + that).
    #  M26 -- '91' was stripped whenever len > 10, so an 11-digit typo ('91167084418') lost its 91 and
    #        became a confident-looking 9-digit key. A real 91-prefixed Indian number is EXACTLY 12
    #        digits, so require that before peeling.
    def _india10(x): return len(x) == 10 and x[0] in '6789'
    while True:
        # strip 91 while it is clearly a dial-code wrapper: either there is still more than a full
        # 91+10 number left (doubled '9191...' entries) or exactly 91 + a plausible Indian mobile
        if d.startswith('91') and (len(d) > 12 or (len(d) == 12 and _india10(d[2:]))):
            d = d[2:]; continue
        if d.startswith('0') and len(d) > 10:
            _t = d.lstrip('0')
            if _india10(_t) or (len(_t) == 12 and _t.startswith('91') and _india10(_t[2:])):
                d = _t; continue
        break
    return d

def norm_phone(p):
    """Canonical phone key. India -> 10-digit national; foreign -> full international digits;
    7-9 digit numbers KEPT (short-number countries; previously discarded); junk/placeholder -> ''."""
    d = digits(p)
    if not d: return ''
    d = peel(d)
    if is_junk_phone(d): return ''
    return d if len(d) >= 7 else ''

def bare10(d):
    """Country-agnostic last-10 key for Indian mobiles, GUARDED: only when the leading
    remainder is an India-consistent prefix ('' / 0 / 91 / 091 / 0091 / 9191) and the
    10-digit tail starts 6-9. Never fires on foreign codes (44..., 971...) -> no cross-country aliasing."""
    d = str(d or '')
    if len(d) >= 10 and d[-10] in '6789' and d[:-10] in ('', '0', '91', '091', '0091', '9191'):
        b = d[-10:]
        return b if not is_junk_phone(b) else None
    return None

def phone_in_email(e):
    """Indian mobile embedded in the email local part (e.g. v9177554973@gmail.com).
    2026-07-29 fix (bug review H5): the old regex was UNANCHORED, so it slid a 10-digit window across any
    longer digit run and invented numbers -- '919876543210@gmail.com' yielded '9198765432' (a different,
    valid-looking mobile, not the real 9876543210) and 'inv98765432101234@x.com' mined '9876543210' out of
    an invoice string. These fed a live match key. Now: take MAXIMAL digit runs only, and hand each to
    norm_phone so dial-code peeling and junk detection do the work -- that also makes the 91-prefixed case
    resolve CORRECTLY instead of being mangled or dropped."""
    e = str(e or '')
    if '@' not in e: return None
    for d in re.findall(r'\d+', e.split('@', 1)[0]):
        if not 10 <= len(d) <= 13:
            continue          # shorter = not a mobile; longer = an id/invoice string, unrecoverable
        b = bare10(norm_phone(d))
        if b: return b
    return None

if __name__ == '__main__':
    T = [
        (canon_email('RAHUL@X.COM'), 'rahul@x.com'),
        (canon_email('rkriti600@gmal.com'), 'rkriti600@gmail.com'),
        (canon_email('bladegaming260@gamil.com'), 'bladegaming260@gmail.com'),
        (canon_email('dubeyricha96@gmail.con'), 'dubeyricha96@gmail.com'),
        (canon_email('rampapu062@gmail.vom'), 'rampapu062@gmail.com'),
        (canon_email('suryakantmandal@gmai.com'), 'suryakantmandal@gmail.com'),
        (canon_email('name @gmail.com.'), 'name@gmail.com'),
        (canon_email('KMUDIT11@GMAIL.COM'), 'kmudit11@gmail.com'),
        (canon_email('not-an-email'), ''),
        (canon_email('keep@company.co.in'), 'keep@company.co.in'),
        (gmail_key('first.last+promo@gmail.com'), 'firstlast@gmail.com'),
        (gmail_key('user@yahoo.com'), None),
        (norm_phone('919845344750'), '9845344750'),
        (norm_phone('09845344750'), '9845344750'),
        (norm_phone('00919845344750'), '9845344750'),
        (norm_phone('91918547097500'), '8547097500'),
        (norm_phone('971506791561'), '971506791561'),
        (norm_phone('8801722866290'), '8801722866290'),
        (norm_phone('16505200993'), '16505200993'),
        (norm_phone('447400232640'), '447400232640'),
        (norm_phone('82399770'), '82399770'),
        (norm_phone('919999999999'), ''),
        (norm_phone('1234567890'), ''),
        (norm_phone('98765'), ''),
        (bare10('9845344750'), '9845344750'),
        (bare10('919845344750'), '9845344750'),
        (bare10('447400232640'), None),
        (bare10('1509969209'), None),
        (phone_in_email('v9177554973@gmail.com'), '9177554973'),
        (phone_in_email('abc@gmail.com'), None),
        (is_junk_phone('9999999999'), True),
        (is_junk_phone('9845344750'), False),
        (is_junk_email('abc@gmail.com'), True),
        (is_junk_email('test@yahoo.com'), True),
        (is_junk_email('xy@gmail.com'), True),
        (is_junk_email('anything@yopmail.com'), True),
        (is_junk_email('info@somecompany.com'), False),
        (is_junk_email('abc@somecompany.com'), False),
        (is_junk_email('rahul.k1988@gmail.com'), False),
        (is_junk_email('7579209537a@gmail.com'), False),
    ]
    T += [
        (digits('9.18149E+11'), ''),
        (digits('919952054272.0'), '919952054272'),
        (digits(919849263781.0), '919849263781'),
        (norm_phone('9.19952E+11'), ''),
        (norm_phone('006582399770'), '006582399770'),
        (bare10(norm_phone('006582399770')) or '', ''),
        (norm_phone('00919845344750'), '9845344750'),
        (norm_phone('91918547097500'), '8547097500'),
    ]
    T += [
        (is_junk_email('abc@gamil.com'), True),
        (is_junk_email('rahul.sharma1988@gmail.com'), False),
        (key_email('First.Last+promo@GMAIL.con'), 'firstlast@gmail.com'),
        (key_email('test@yahoo.com'), ''),
        (key_email('rahul.k@gmial.com'), 'rahulk@gmail.com'),
        (key_email(''), ''),
    ]
    T += [(canon_email('abc@gmail'), ''), (key_email('abc@gmail'), '')]
    # ---- 2026-07-29 Phase 1 (bug review H4/H5/L1/L2/L4). These are the exact gaps that let the
    # ---- fixed defects live: the old 56-test suite never folded a junk key, never fed phone_in_email a
    # ---- digit run longer than 10, never passed digits() a float, and never used an invisible char.
    T += [
        # H4 -- the dot/+tag fold must not LAND on a blacklisted key
        (key_email('a.b.c@gmail.com'), ''),
        (key_email('t.e.s.t+x@gmail.com'), ''),
        (key_email('x.y@gmail.com'), ''),
        (key_email('a.d.m.i.n@gmail.com'), ''),
        (key_email('rahul.k1988@gmail.com'), 'rahulk1988@gmail.com'),   # a real address still folds
        # H5 -- no sliding window over longer digit runs; 91-prefixed resolves correctly.
        # (NB: 9876543210 is in _PLACE, so it is deliberately unusable as a key -- use a real-shaped number.)
        (phone_in_email('919845344750@gmail.com'), '9845344750'),
        (phone_in_email('inv98453447501234@x.com'), None),
        (phone_in_email('9845344750@gmail.com'), '9845344750'),
        (phone_in_email('v9177554973@gmail.com'), '9177554973'),
        (phone_in_email('order12345@x.com'), None),
        (phone_in_email('9876543210@gmail.com'), None),   # placeholder stays refused
        # L1/L2 -- float cells that already lost precision are unrecoverable, not fake keys
        (digits(918149000000.0), ''),
        (norm_phone(918149000000.0), ''),
        (digits(919845344750.4), ''),
        (digits(919849263781.0), '919849263781'),                        # genuine float cell still works
        (norm_phone(919849263781.0), '9849263781'),
        # L4 -- both invisible-char sets cover the union (U+2060 and NBSP)
        (canon_email('rahul⁠k@gmail.com'), 'rahulk@gmail.com'),
        (canon_email('rahul\xa0k@gmail.com'), 'rahulk@gmail.com'),
        (key_email('rahul⁠k1988@gmail.com'), 'rahulk1988@gmail.com'),

        # ---- 2026-08-05 bug-review additions. The 75-case suite passed while every one of these
        # was broken, because it only ever fed the NUMERIC spelling of a mangled phone and only ever
        # probed providers that were already in _FREEMAIL. Each block pins a branch, not a symptom.
        # H8 -- the string spelling of every numeric case (csv.reader yields STRINGS, so this is the
        # dominant path, and it bypassed both mangle guards)
        (digits('919845344750.4'), ''),          # was '9198453447504' -> key '98453447504'
        (norm_phone('919845344750.4'), ''),
        (digits('918149000000.0'), ''),          # was '918149000000' -> key '8149000000'
        (norm_phone('918149000000.0'), ''),
        (digits(919845344750.4), ''),            # float path, unchanged
        (digits('9.18149E+11'), ''),
        # H12 -- an int lost no precision, so a real number ending in five zeros must survive
        (digits(918496800000), '918496800000'),
        (norm_phone(918496800000), '8496800000'),
        (norm_phone('918496800000'), '8496800000'),
        (norm_phone(917096300000), '7096300000'),
        (digits(918496800000.0), ''),            # ...but the FLOAT spelling is still refused
        # M25 -- vanity numbers are not placeholders; a Rs 16,014 payer sits on this one
        (norm_phone("'+918080000888'"), '8080000888'),
        (norm_phone('918484848488'), '8484848488'),
        (is_junk_phone('9999999999'), True),     # a real placeholder still is one
        (is_junk_phone('0000000000'), True),
        (is_junk_phone('8080000888'), False),
        # M26 -- 91 is a dial code only on a 12-digit number; an 11-digit typo keeps its digits
        (norm_phone('91167084418'), '91167084418'),
        (norm_phone('91918547097500'), '8547097500'),   # doubled 91 still peels
        (norm_phone('918218612326'), '8218612326'),
        # H9 -- foreign numbers in INTERNATIONAL form stay out of the Indian bare-10 keyspace
        (bare10(norm_phone('447400232640')), None),
        (bare10(norm_phone('971558007879')), None),
        (bare10(norm_phone('8801722866290')), None),
        # H10 -- generic locals on providers that were missing from _FREEMAIL
        (is_junk_email('abc@protonmail.com'), True),
        (is_junk_email('test@rediff.com'), True),
        (is_junk_email('abc@yahoo.co.uk'), True),
        (is_junk_email('demo@outlook.in'), True),
        (is_junk_email('xxx@gmail.com'), True),
        (is_junk_email('temp@gmail.com'), True),
        (is_junk_email('12345@gmail.com'), True),
        (is_junk_email('ravi.kumar@gmail.com'), False),   # a real address is untouched
        (is_junk_email('ceo@somecompany.com'), False),    # corporate role addresses are real people
        # every provider canon_email repairs must also be junk-checkable, or the two drift apart
        (all(any(f.split('.')[0] == p for f in _FREEMAIL) for p in _PROV), True),
    ]
    bad = [(i, g, w) for i, (g, w) in enumerate(T) if g != w]
    for i, g, w in bad: print('FAIL #%d: got %r want %r' % (i, g, w))
    print('self-test: %d/%d passed' % (len(T) - len(bad), len(T)))
    raise SystemExit(1 if bad else 0)
