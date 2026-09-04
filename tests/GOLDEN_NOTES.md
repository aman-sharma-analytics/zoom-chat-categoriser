# Golden-test notes — divergences vs `Lead Score_2026-08-02.xlsx`

Measured 2026-08-05 on the 2 Aug session (46,020 matched people):

| Metric | Result |
|---|---|
| Engagement category (aligned) | **99.64%** |
| Relevant chat (exact string) | **99.98%** |
| Deleted chat (exact string) | **99.98%** |

Every mismatch was investigated. None is a port bug; each falls into one of the
documented classes below. The regexes, `_msg_signals`, and `categorize` were verified
**AST-identical** to the engine source, and `chat_cleaner`/`contact_norm` are verbatim
copies passing their own self-tests (123/123, 107/107).

## Deliberate differences (the build plan mandates them)

1. **`non chatted` → `non attended` (5,120 rows).** `Attended = No` registrants.
   The published file shows them as attendee-side rows ("no chat messages; barely
   present"); build plan §4.2 says they must publish as **non attended** and never
   contribute a room, chat candidacy, or attendance time. The tool follows the plan.

2. **No cross-registration dedup (~776 tool-only rows, ~9 chat cells).** Production's
   invited report folds twin registrations (typo'd email + real email of the same
   person) into one row via `_dedup_same_person`, which needs the invited sheet's
   context. This tool publishes one row per unique contact key, as §7 specifies
   ("Row count = unique people across all rooms").

## Known engine quirks the tool intentionally does NOT reproduce (~0.5% of rows)

3. **Dims-on-name-winner vs dims-on-attributed-messages (~150 rows).** The engine
   computes D1/D3/D4/D5/D7 and the Intent/Meaningful/Neg counts over the NAME-keyed
   chat corpus and attaches them to the longest-present same-name record, while the
   displayed chat comes from per-message contact attribution. When a person renamed
   mid-session or two same-name people share a room, the published row can carry
   counts computed over a different message set than the chat it displays
   (e.g. `mm=2` next to 4 shown messages). The tool computes the counts over exactly
   the messages it attributes — the build plan's §5 step 1–2 design — so a category
   that hangs on a threshold (`mm >= 3`, `D4 > 0`) occasionally lands one bucket away.

4. **`mm >= 1 & stayed-to-CTA & present-at-pricing → moderate interest` branch.**
   Contrary to plan §2, this ONE branch of `categorize()` does read the CTA/pricing
   flags (invited_report.py line ~388). Without offsets those flags are blank and such
   rows land in `no clear intent`/`information seeking` instead of `moderate
   interest`. Supplying the optional offsets in the UI restores production behaviour;
   the golden test passes windows derived from the shared workshop transcript
   (pricing ≈ minute 164.6–173.0, CTA ≈ minute 202.8–212.0).

5. **Multi-room presence (~a few dozen rows).** The tool merges a person's minutes
   across rooms (sum ÷ longest room duration); production keeps one room's record.
   A person who left room A early but returned in room B can differ on
   `% attended`-driven basis text and the CTA/pricing flags.

## Cross-identity phone fallback (kept, engine behaviour)

A row whose email never keyed a message borrows the chat of the unique
chat-receiving identity that shares its phone (`by_phone`), marked `orphan` — this is
the engine's `_corpus_msgs` fallback and explains why a handful of people in the
19 Jul no-chat rooms still show chat: they genuinely typed it, in another room,
under their other registration.

## 2026-09-03 whole-tool checkup: deliberate behaviour that reads like a bug

Both of these were investigated against all 11 archived exports and deliberately LEFT
ALONE. Do not "fix" either without re-running the measurements named here.
`tests/test_checkup.py` asserts them so a future change has to argue with the evidence.

1. **A 10-digit number is never split, whatever the Country cell says.**
   Zoom's `Country/Region Name` records where the person *lives*, so an NRI's 10-digit
   Indian mobile arrives tagged with a foreign country. 33 rows across the archived
   sessions look like a duplicated dial code (`+65-6597436884`); **31 of them are valid
   10-digit Indian mobiles** (start 6-9), and peeling would delete real digits from a
   dialable lead. `cc_split` therefore only peels numbers of 12+ digits, and only when
   the Country cell is empty — with a country named, that cell is the better evidence.
   Governing invariant, verified over 599,848 real phone rows: **the published Phone
   Number always still contains every digit of the stored number** (0 losses).

2. **An attendee message containing `" | "` is split across cells.**
   `all_chats` uses `" | "` as its separator verbatim from the engine. Matching the
   engine wins over cosmetics here; changing the separator would break parity.

## 2026-09-03 checkup: fixes that changed behaviour

None of these moved the golden numbers (cat 99.69% / rel 99.92% / del 99.99% before and
after) and parity stayed AST-clean, because none touch a category decision.

- **Inverted join/leave segments are no longer stored** (`parse_attendee.aggregate_text`).
  Zoom exports rows whose Leave precedes its Join, with its own `Time in Session` going
  negative (-178, -133, -83 seen). `merged_minutes` always discarded them, but
  `seg_list`/`name_segs` kept them, so the three views disagreed: **107 people** whose
  every segment is inverted published a confident `0 minutes / 0% attended` instead of
  "unknown", and the chat presence-window test ruled such a candidate *out* rather than
  admitting it could not place them — misattributing **56 chat lines across 4 same-name
  groups** in 3 sessions. Attribution now also refuses to resolve a same-name group past
  a candidate it cannot place in time at all (drop-never-guess).
- **Blank/unmapped country no longer stamps +91 onto an international number.** The same
  UAE number published as `+971-545512993` with the country filled and
  `+91-971545512993` without it. 64 country names present in the real exports were
  missing from `COUNTRY_CC` entirely (223 rows); all are now mapped. 323 rows (0.054%)
  change value, none of them losing a digit.
- **Attendee-header discovery is NUL-tolerant.** `_csv_row` repaired NULs, but only after
  a line was recognised, so one NUL inside `Attended,` made the header undiscoverable and
  discarded every attendee in that room.
- **`preflight.check_rooms` no longer crashes** (`UnboundLocalError: dur`) when a room's
  CSV parses 0 rows and a chat export is present; `dur` is also no longer inherited from
  the previous room.
