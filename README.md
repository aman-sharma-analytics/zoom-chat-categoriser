# Zoom Chat Categoriser

A local web tool: Zoom **attendee reports** + **chat exports** for one workshop session
in → a spreadsheet out. Everything runs on this machine; nothing is uploaded anywhere.

**Output columns (agreed 2026-09-03):**

| # | Column | Source |
|---|---|---|
| 1 | Activity Date | typed on the dashboard as `dd:mm:yy hh:mm`; blank falls back to the session's real start time |
| 2 | Email | attendee report |
| 3 | Phone Number | `+cc-number` (dial code split out by `contact_norm`, then rejoined) |
| 4 | Session Name | typed on the dashboard, written onto every row |
| 5 | Session Engagement | the 8-way category (`categorize()`, engine parity v4.6) |
| 6 | Zoom chat | the substantive part of the person's chat (`chat_cleaner`) |

Tick **Include debug columns** to also get `Customer name`, `Category Basis`,
`Confidence`, `Removed chat`, `Zoom room`, `Attended?`, `Minutes present`,
`Message count` — nothing is lost from the deliverable, it is one checkbox away.

Built by **extracting and wiring the be10x lead-scoring engine's validated logic** —
not re-implementing it (see the build plan). `core/chat_cleaner.py` and
`core/contact_norm.py` are verbatim copies (self-tests 123/123 and 107/107);
`categorize.py` is an AST-verified port of the engine's 8-way categoriser;
`dimensions.py` carries the phrase-bank matcher; `config/phrase_banks.json` is lifted
from `scoring_formula.json`.

## Run

Double-click **run.bat**, or:

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

then open http://127.0.0.1:8010 and upload through the two blocks:

- **Zoom Attendee Report** — the attendee CSV export (Zoom → Reports → Attendee Report)
- **Zoom Save Chat** — the saved chat `.txt` (`meeting_saved_chat.txt`)

One report + one chat file = one session. Several rooms? Upload one report and one
chat file per room — they pair **in the order added** (1st report + 1st chat = Room 1),
and a count mismatch is refused rather than mis-attributed. Chat is optional (a
no-chat session publishes everyone as `non chatted`). The same meeting saved in both
Zoom chat formats is detected and deduped. Session zips (room subfolders) are still
accepted via the API's `files` field.

Dependencies (`pip install -r requirements.txt`): fastapi, uvicorn, python-multipart,
xlsxwriter (+ openpyxl for the golden test only).

## What you get

1. **Pre-flight verdicts** per room *before* anything is scored — NUL-damaged CSVs,
   truncated chat exports, missing attendee reports, missing chat, registration-only
   exports. A FAIL means that room would silently degrade; you can still proceed
   knowingly.
2. The **8-way engagement category** per person (priority-ordered, mutually
   exclusive): `non attended → non chatted → negative engagement → purchase intent
   high → strong interest → moderate interest → information seeking → no clear
   intent`. A row cannot reach the top buckets on presence alone without purchase
   language in their actual chat — deliberate engine behaviour.
3. **Relevant / Removed chat** — the engine's chat cleaner v1.3, strict split: a
   person whose every message is junk gets a *blank* Relevant Chat by design.
4. A **Run Report sheet** in the workbook: per-room stats, attribution counts
   (including the % of messages dropped as unattributable), audit counters, warnings.
5. Optional **pricing / CTA minute offsets** — they enrich the category-basis
   evidence ("present at pricing", "stayed to CTA"); categories work without them.
6. Optional **debug columns** (category basis, Zoom room, attended?, minutes,
   message count) behind a checkbox.

The **chat-clean audit gate** (`bad_chars` / `repeats` / `vocab_leaks`) must be 0/0/0
or the download is blocked — junk in Relevant Chat must not ship.

## What this tool will NOT do

- No Lead Score, no A/B/C/D tiers (needs the invited all-data sheet + payment feed).
- No cross-week persistence — single session only.
- No payment / conversion matching.
- Phone may be blank if the Zoom export has no phone column (it will say so).
- ~1–2% of chat is unattributable (same-name registrants in one room) and is
  **dropped, never guessed**; the exact number is shown after each run.

## Tests

```bash
python -m tests.test_selftests     # fast, no data needed — must be ALL PASS
python -m tests.test_golden       # needs the "Lead scoring" folder; several minutes
```

Golden result (2 Aug session, 46,020 matched people): categories 99.64% aligned,
relevant/deleted chat 99.98% exact vs the published `Lead Score_2026-08-02.xlsx`.
Divergences are documented in `tests/GOLDEN_NOTES.md` — none is a port bug.

## Layout

```
app.py                  FastAPI wrapper (upload → preflight → process → download)
templates/index.html    the one-page UI
core/
  chat_cleaner.py       COPIED VERBATIM — do not edit
  contact_norm.py       COPIED VERBATIM — do not edit
  categorize.py         AST-verified port of the engine's categorize() + _msg_signals()
  dimensions.py         phrase-bank matcher + D1/D3/D4/D5/D7 scorer + paste guard
  parse_attendee.py     multi-section, NUL-tolerant attendee CSV parsing
  parse_chat.py         both chat formats, midnight wrap, reply-quote exclusion
  attribute.py          chat → person (presence-window disambiguation, phone fallback)
  pipeline.py           orchestrator + invariants + audit gate
  build_xlsx.py         writer (shared-strings mode) + Run Report sheet
  preflight.py          upload validator (port of the engine's preflight)
config/phrase_banks.json  lifted from scoring_formula.json — edit carefully; empty
                          banks are refused at load
```
