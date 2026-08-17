# -*- coding: utf-8 -*-
"""Chat -> person attribution (contact-keyed, within one room).

Port of the be10x engine's build_contact_corpus (invited_report.py v5.4/v5.5), the layer
that replaced the name-only global join. The rules, each validated in production:

  * A chat line carries a DISPLAY NAME, not an identity. It resolves to a contact only
    INSIDE ITS OWN ROOM, against that room's attendee CSV.
  * Candidates are Attended=Yes registrants with a USABLE email key (contact_norm.key_email;
    junk/shared addresses like abc@gmail.com are refused rather than merging strangers).
    Admitting Attended=No rows manufactures ambiguity from people who were never in the
    room (2 Aug: +59% same-name candidate sets).
  * A name shared by 2+ registrants in one room is resolved PER MESSAGE by presence
    window (join <= t <= leave, with 90s grace). Validated: 99.5% of unambiguous senders'
    messages fall inside their own span.
  * A message that cannot be pinned to exactly ONE candidate is DROPPED from scoring --
    never guessed. It is kept, marked, for display only (the '[unattributed -- one of N
    same-name registrants]' pool). Expect ~1-2% of messages; the number is surfaced.

Also provides the ENGINE-style name view (which display names chatted; who is the
longest-present record for a name) -- the published 'Chatted?' flag and the no_text
guard in categorize() are defined against that view, so the port must carry it too.
"""
import datetime
from collections import Counter

from . import contact_norm as CN
from .parse_chat import is_host_sender, norm_name

GRACE = 90   # seconds of slack on each side of a join/leave window (clock rounding)


def build_room_attribution(recs, meta, blocks):
    """One room's (recs, meta, chat blocks) -> attribution result.

    recs:   {person_key: rec} from parse_attendee.aggregate_text
    meta:   room meta (start anchors the chat clock to a real date)
    blocks: parse_chat.parse_chat_text blocks (host senders are skipped here)

    Returns dict:
      by_key:  {key_email: [block, ...]}   blocks attributed to exactly one contact
      unattr:  {key_email: (lines, n_candidates)}  same-name leftovers, display only
      stats:   Counter (sender_unique / sender_time_resolved / sender_unresolved /
               msg_unique / msg_time_resolved / msg_still_ambiguous / msg_no_csv_row /
               sender_no_csv_row / host_blocks)
      name_chat: set of chatted norm names (non-host)
      winners: {norm_name: person_key} longest-present attendee record per name
               (the engine's by_norm chat-link winner)
    """
    S = Counter()
    # ---- candidate map: norm(RAW row name) -> {key_email: [(join, leave), ...]}
    # Registered PER ROW-NAME VARIANT with that name's own segments (engine rule): people
    # rename mid-session, and their chat arrives under whichever name was in force. RAW
    # norms only -- also indexing the cleaned-name norm MANUFACTURES same-name ambiguity
    # (one person becomes two candidates) and drops real messages.
    byname = {}
    phones_of = {}                         # key_email -> set of normalised phones (fallback map)
    for pk, rec in recs.items():
        if not rec.get('attended_flag'):
            continue                       # Attended=No: never a chat candidate
        ke = CN.key_email(rec.get('email'))
        if not ke:
            continue                       # no usable contact key -> cannot receive chat
        name_segs = rec.get('name_segs') or {}
        if not name_segs and rec.get('orig'):
            name_segs = {rec['orig']: list(rec.get('seg_list') or [])}
        any_name = False
        for raw_nm, segs in name_segs.items():
            nm = norm_name(raw_nm)
            if not nm:
                continue
            any_name = True
            byname.setdefault(nm, {}).setdefault(ke, []).extend(segs)
        if any_name and rec.get('phone'):
            phones_of.setdefault(ke, set()).add(rec['phone'])

    # ---- chat clock anchor: the room's own session date
    base = None
    if meta.get('start'):
        base = datetime.datetime.combine(meta['start'].date(), datetime.time())
    else:
        joins = [r['first_join'] for r in recs.values() if r.get('first_join')]
        if joins:
            base = datetime.datetime.combine(min(joins).date(), datetime.time())

    # ---- group blocks by sender
    senders = {}
    for b in blocks:
        if is_host_sender(b['sender']):
            S['host_blocks'] += 1
            continue
        senders.setdefault(norm_name(b['sender']), []).append(b)

    by_key = {}
    unattr = {}
    name_chat = set()
    for nmz, blist in senders.items():
        name_chat.add(nmz)
        nmsgs = sum(len(b['lines']) for b in blist)
        cands = byname.get(nmz)
        if not cands:
            S['sender_no_csv_row'] += 1
            S['msg_no_csv_row'] += nmsgs
            continue
        if len(cands) == 1:
            ke = next(iter(cands))
            by_key.setdefault(ke, []).extend(blist)
            S['sender_unique'] += 1
            S['msg_unique'] += nmsgs
            continue
        # same-name group: resolve per block by presence window
        hit = False
        left = []
        g = datetime.timedelta(seconds=GRACE)
        for b in blist:
            t = (base + datetime.timedelta(seconds=b['sec'])) if (base and b['sec'] is not None) else None
            if t is None:
                left.extend(b['lines'])
                S['msg_still_ambiguous'] += len(b['lines'])
                continue
            live = [k for k, ivs in cands.items()
                    if any(iv and iv[0] - g <= t <= iv[1] + g for iv in ivs)]
            if len(live) == 1:
                by_key.setdefault(live[0], []).append(b)
                S['msg_time_resolved'] += len(b['lines'])
                hit = True
            else:
                left.extend(b['lines'])
                S['msg_still_ambiguous'] += len(b['lines'])
        if left:
            # kept against EVERY same-name candidate, marked, display only --
            # never fed to categorisation
            for k in cands:
                p = unattr.get(k)
                unattr[k] = ((p[0] if p else []) + left, max(len(cands), p[1] if p else 0))
        S['sender_time_resolved' if hit else 'sender_unresolved'] += 1

    # ---- engine-style winner per name (longest minutes_present; primary norm beats alias)
    winners = {}
    best = {}
    for pk, rec in recs.items():
        if not rec.get('attended_flag'):
            continue
        prim = rec.get('norm') or ''
        alias = norm_name(rec.get('orig') or '')
        for nm, is_prim in ((prim, True), (alias, False)):
            if not nm:
                continue
            mp = rec.get('minutes_present') or 0
            cur = best.get(nm)
            # primary-norm entries always beat alias-only ones; then longest presence
            rankv = (1 if is_prim else 0, mp)
            if cur is None or rankv > cur[0]:
                best[nm] = (rankv, pk)
    for nm, (_, pk) in best.items():
        winners[nm] = pk

    # ---- phone fallback map (engine's by_phone): phone -> key_email, only where the
    # phone belongs to exactly ONE email key that RECEIVED chat (the owner map is built
    # over chat receivers only -- a silent registrant sharing the phone must not make it
    # look 'shared', that is exactly the row the fallback exists to serve).
    owner = {}
    for ke, phs in phones_of.items():
        if ke not in by_key:
            continue
        for p in phs:
            owner.setdefault(p, set()).add(ke)
    by_phone = {p: next(iter(es)) for p, es in owner.items() if len(es) == 1}
    S['phones_dropped_shared'] = sum(1 for es in owner.values() if len(es) > 1)

    return {'by_key': by_key, 'unattr': unattr, 'stats': S,
            'name_chat': name_chat, 'winners': winners, 'by_phone': by_phone}
