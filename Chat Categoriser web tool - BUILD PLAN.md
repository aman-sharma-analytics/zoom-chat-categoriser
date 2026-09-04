# Zoom Chat Categoriser — Build Plan

**For:** Claude Code
**Author:** written 5 Aug 2026 from the live be10x lead-scoring engine
**Goal:** a local web tool that takes Zoom attendee reports + Zoom chat reports for one workshop session and produces a 6-column spreadsheet: name, email, country code, phone, engagement category, relevant chat, removed chat.

---

## 0. Read this first — the single most important instruction

**Do not re-implement the chat cleaning or the engagement categorisation from scratch.** Both already exist, are deterministic, stdlib-only, and have been validated against seven weeks of real production output (~350,000 rows). They live in:

```
_lead_scoring_engine/The core modules/chat_cleaner.py     (v1.3, self-test 123/123)
_lead_scoring_engine/The core modules/contact_norm.py     (self-test 107/107)
_lead_scoring_engine/The core modules/invited_report.py   (categorize() + its regex constants)
_lead_scoring_engine/The core modules/run.py              (chat parsing + D1..D8 dimension scorer)
_lead_scoring_engine/The core modules/scoring_formula.json (phrase banks the dimensions read)
```

Your job is to **extract and wire**, not to invent. Every regex in those files encodes a specific real-world failure that was found by reading actual chat transcripts — Hindi/Tamil/Telugu handling, "paid" vs "high paying job", tool-price questions vs programme-price questions, AV complaints that must not count as negativity. Rewriting them from intuition will silently produce worse output that still looks plausible.

**Copy `chat_cleaner.py` and `contact_norm.py` verbatim into the new project.** They have no dependencies beyond the standard library and no knowledge of the surrounding pipeline. Run their self-tests in CI.

---

## 1. Decisions already made (do not re-litigate)

| Decision | Choice |
|---|---|
| Deployment | **Local Python web app** (FastAPI or Flask), opened at `localhost`. Nothing is uploaded anywhere. |
| Scope per run | **Whole session at once** — several room pairs, or a session zip |
| Pricing/CTA timing | **Optional** offsets typed into the UI (see §6 — this turned out to matter less than expected) |
| Phone column | **Adaptive** — detect it, use it if present, blank + explain if absent |

---

## 2. Correction to an earlier assumption — read this, it simplifies the build

I originally said the engagement category needed "present at pricing" and "stayed to CTA", and therefore needed a transcript. **That was wrong, and I verified it against the code.**

`categorize()` reads exactly these fields:

```
Attendee, Chatted?, % attended,
D1, D3, D4, D5, D7,
Intent msgs, Meaningful msgs, Neg msgs,
plus the full list of that person's chat messages
```

`Stayed to CTA` and `Present at pricing` **are** read — but only to build the human-readable "Category Basis" explanation string. They never participate in a return decision. Grep it yourself: the only three uses are `watch.append(...)`, `watch.append(...)` and `ctx = [...]`.

**Consequence:** the engagement category is 100% computable from the attendee report + chat report alone. The pricing/CTA offsets are a *nice-to-have* that improve the explanation text, not a prerequisite. Build the UI fields, mark them optional, and let the tool work without them.

---

## 3. Inputs

### 3a. Zoom attendee report (CSV, one per room)
Multi-section file. Sections are separated by their own header rows; there are typically **three** header rows in one file (host details, panelist details, attendee details). You must find *every* row where a cell equals `Attended` and parse each section with **its own column indices** — the sections do not share a layout. Getting this wrong silently yields a handful of rows instead of thousands.

Columns used: `Attended` (Yes/No), `User Name (Original Name)`, `Email`, `Phone` (may be absent), `Join Time`, `Leave Time`, `Time in Session (minutes)`, and from the meta block `Topic`, `Actual Start Time`, `Actual Duration (minutes)`.

### 3b. Zoom chat report (TXT, one per room)
Line format, both variants in the wild:
```
HH:MM:SS From <Sender Name> to <Recipient>:
YYYY-MM-DD HH:MM:SS From <Sender Name> to <Recipient>:
```
followed by one or more body lines. Use the exact regex already in the engine (`run.py:321` / `invited_report.py:403`):
```python
HEADER = re.compile(r'^(?:\d{4}-\d{2}-\d{2}\s+)?(\d{1,2}:\d{2}:\d{2})\s+From\s+(.+?)\s+to\s+(.+?):\s*$')
REPLY_QUOTE = re.compile(r'^\s*(?:Replying to |Reacted to |Removed a )"?')
```
`REPLY_QUOTE` lines are **the other person's words** — never credit them to the replier/reactor. Handle the clock crossing midnight (timestamps wrap; add 24h).

---

## 4. Hard-won parsing rules — each of these was a production incident

Implement all of them. Every one cost real data.

1. **NUL bytes.** One `\x00` in an attendee CSV makes `csv.reader` raise `_csv.Error: line contains NUL` and abort the *entire file*. A real export (12 Jul / Ops 6) had 48 of them and lost a whole room — about 4,800 people. Parse per row; on failure strip NULs and retry; only if that fails, skip that one row and count it. **One bad byte must cost one row, not a room.**

2. **`Attended = No` means they never joined.** Those rows exist in the report as registrations. Include them in the output as category `non attended`, but **never** let them contribute a room, a chat candidate, or attendance time. Admitting them previously put a phantom room on 587 people and manufactured same-name ambiguity for 840 sender groups.

3. **Chat gives you a display name, not an identity.** Join chat → person **within the same room only**, by name, and disambiguate same-name registrants using the presence window (join ≤ message time ≤ leave, with ~90s grace). If a message cannot be pinned to exactly one person, **drop it — never guess**. Expect ~1–2% unresolvable. Surface that number in the UI.

4. **Truncated chat exports look exactly like a quiet room.** If one room's share of attendees carrying chat is a fraction of the others' (1.4% vs 30–50%), the export is truncated, not the audience silent. Compare the chat file's first/last timestamp span against session duration and **warn loudly** rather than shipping a room where everyone reads as silent.

5. **Do not modify the raw chat.** Keep the verbatim concatenation intact; Relevant/Removed are derived views.

---

## 5. The categorisation pipeline

### Step 1 — build each person's message list
`{(room, person_key): [msg, msg, ...]}` where `person_key` is the email where available. Use `contact_norm.key_email()` for the key — it folds Gmail dots/plus-tags, repairs typo domains (`gamil.com`, `gmail.con`) and refuses junk shared addresses (`abc@gmail.com`) that would otherwise merge strangers.

### Step 2 — compute the dimension inputs
`categorize()` needs `D1, D3, D4, D5, D7` and the three message counts. These come from the phrase-bank matcher in `run.py` (`compile_cfg()` at line 262, `score()` at ~line 515) driven by `scoring_formula.json` → `phrase_banks` + `negativity_patterns`.

- Extract `compile_cfg()` and the `D1/D3/D4/D5/D7` portion of `score()`.
- **You do not need D2, D6, D8** — `categorize()` never reads them. D6 is cross-week persistence, which you don't have anyway.
- Copy `phrase_banks` and `negativity_patterns` out of `scoring_formula.json` into a config file the tool ships with.
- **Guard:** an empty phrase bank compiles to `re.compile('')`, which matches *every* string. Refuse an empty or blank-containing bank at load with a named error.

### Step 3 — call `categorize()`
Signature: `categorize(row, ix, msgs=None, orphan=False, no_text=False)`.
Port it together with its module-level regex constants — `PROGBUY`, `PAYCONF`, `PAYNEG`, `BUILDPAY`, `TOOLPRICE`, `COURSEQ`, `SESSDUR`, `FOLLOWUP`, `TECHNOISE`, `SEVERE`, `_msg_signals()` — and the `_HAND` override table (leave it empty).

**Critical behaviour to preserve:** when `msgs` is `None`, `categorize()` drops its text-corroboration guards and over-reports "strong interest". Always pass the real message list, and pass `no_text=True` for a person with no attributable text.

The eight categories, priority-ordered and mutually exclusive:
```
non attended → non chatted → negative engagement → purchase intent high
→ strong interest → moderate interest → information seeking → no clear intent
```
Note the design rule: a row **cannot** reach `purchase intent high` or `strong interest` on presence signals alone without actual purchase language in the text. This is deliberate — do not "improve" it.

### Step 4 — split the chat
```python
import chat_cleaner
r = chat_cleaner.process_cell(all_chats_string)   # all_chats = " | ".join(messages)
r["relevant"], r["deleted"]
```
`SEP` is `" | "`. **Strict split is intentional:** a lead whose every message is junk gets a *blank* Relevant Chat — roughly 3.7k rows/week in production. Do not add a "rescue" that back-fills them; that was explicitly retired.

Run `chat_cleaner.audit_relevant(all_relevant_cells)` after the build. All three counters (`bad_chars`, `repeats`, `vocab_leaks`) must be **zero**. Non-zero means junk leaked — **block the download and show why**, don't just warn. `freq_suspects` is a *suggestion* list for human review; never auto-add to the vocabulary.

---

## 6. Optional pricing / CTA offsets

Two number inputs, minutes from session start (e.g. pricing 164, CTA 202). If supplied, mark `present at pricing` / `stayed to CTA` per person from their join/leave intervals against `Actual Start Time`, and pass them through so the Category Basis text is richer. If blank, everything still works — see §2.

---

## 7. Output

Columns, in this order:

| # | Column | Source |
|---|---|---|
| 1 | Customer name | `User Name (Original Name)` |
| 2 | Customer email | `Email` |
| 3 | Country code | `contact_norm` split; `+91` default when the number is Indian-shaped and no code is present |
| 4 | Phone number | national number, 10 digits for India |
| 5 | Engagement category | `categorize()` |
| 6 | Relevant chat | `process_cell()["relevant"]` |
| 7 | Removed chat | `process_cell()["deleted"]` |

Write `.xlsx` with `xlsxwriter`, **`constant_memory=False`** so it produces a real shared-strings table rather than inline strings — the latter bloats the file and some tools handle it badly.

Suggested extras, off by default behind a checkbox: `Zoom Room`, `Attended?`, `Minutes present`, `Message count`. Useful for debugging attribution without cluttering the deliverable.

### Invariants to assert before offering the download
- Relevant/Removed populated **only** where the person actually has chat
- Zero chat text on a row categorised `non chatted` or `non attended`
- Category is one of the eight exact strings
- Row count = unique people across all rooms
- `audit_relevant` returns 0/0/0

---

## 8. Suggested project shape

```
zoom-chat-categoriser/
  app.py                  FastAPI/Flask: upload, process, download
  templates/index.html    drop zone, offsets, options, results table
  core/
    chat_cleaner.py       COPIED VERBATIM — do not edit
    contact_norm.py       COPIED VERBATIM — do not edit
    categorize.py         extracted from invited_report.py
    dimensions.py         extracted from run.py (compile_cfg + D1/D3/D4/D5/D7)
    parse_attendee.py     multi-section, NUL-tolerant CSV reader
    parse_chat.py         HEADER/REPLY_QUOTE, midnight wrap
    attribute.py          chat → person, presence-window disambiguation
    build_xlsx.py         writer + invariant assertions
  config/
    phrase_banks.json     lifted from scoring_formula.json
  tests/
    test_selftests.py     chat_cleaner 123/123, contact_norm 107/107
    test_golden.py        see §9
  requirements.txt        fastapi/flask, uvicorn, xlsxwriter, python-multipart
```

---

## 9. Testing — use the real files, not synthetic ones

You have seven sessions of ground truth. **Use them.**

**Golden-output test.** Take `RAW FILES/2 August Workshop Data.zip` and `output folder/Lead Score_2026-08-02.xlsx`. For every person the tool outputs, compare its `Engagement Category`, `Relevant Chat` and `Deleted Chat` against that published file. They should match for the overwhelming majority. Investigate every mismatch — each one is either a port bug or a genuine consequence of not having the invited sheet.

**Known-answer regression on the inputs:**

| Session / room | Expected behaviour |
|---|---|
| 12 Jul / Ops 6 | 48 NUL bytes — must parse **14,377** rows, not 7,539, and warn |
| 2 Aug / Zoom 11 | chat spans ~7 min of a ~200 min session — must **warn: truncated** |
| 21 Jun / Evening | poll CSV only, no attendee report — must fail cleanly, not crash |
| 19 Jul / OPS 3, OPS 5 | no chat export — every attendee `non chatted`, no error |

**Script-survival test.** These must all survive cleaning and appear in Relevant Chat, not vanish:
```
क्या यह फ्री है?   मेरा पैसा रिटर्न कर   நன்றி   ధన్యవాదాలు   میں شامل ہونا چاہتا ہوں
```
A regex typo once destroyed every non-Latin message — they landed in *neither* Relevant nor Removed. `_ALNUM` must be `r"[^\W_]"`, never an explicit ASCII range.

**Word-mangling test.** `Tata Motors` must not become `Ta Motors`; `Papa`, `Baba`, `murmur` must survive intact.

There is a ready-made pre-flight at `_lead_scoring_engine/The core modules/preflight.py`. Run it against the seven zips: it must flag exactly 12 Jul/Ops 6 (WARN), 19 Jul/OPS 3, 19 Jul/OPS 5, 2 Aug/Zoom 11, 21 Jun/Evening (FAIL) and nothing else. **Port it as the tool's upload validator** — it is the cheapest possible protection against silently scoring a broken export.

---

## 10. Build order

1. `parse_attendee.py` + `parse_chat.py`, tested against real zips. Row counts must reconcile.
2. Copy `chat_cleaner.py` / `contact_norm.py`; wire their self-tests into CI. **Nothing else until these pass.**
3. `attribute.py` — chat → person. Report resolved / window-resolved / unresolved counts.
4. `dimensions.py` + `categorize.py`. Diff against the published 2 Aug file until the category distribution matches.
5. `build_xlsx.py` with the §7 invariants as hard assertions.
6. Flask/FastAPI wrapper + a plain HTML page. UI last — the logic is the hard part.
7. Port `preflight.py` as the upload validator.

---

## 11. What this tool will *not* do

State this in the UI so nobody assumes otherwise:

- **No Lead Score, no A/B/C/D tiers.** Those need the invited all-data sheet (segment, age band, pre-approved amount) and the payment feed. Not derivable from Zoom exports.
- **No cross-week persistence.** Single-session only.
- **No payment / conversion matching.**
- **Phone may be blank** if the Zoom export has no phone column — the tool will say so explicitly rather than shipping a silently empty column.
- **~1–2% of chat unattributable**, by design. Dropped rather than guessed.

---

## 12. Things that will bite you

- `csv.reader` on a NUL → aborts the whole file (§4.1)
- One attendee CSV holds **three** header sections with different layouts
- `chat_cleaner.process_cell` takes the **joined** string, not a list
- `categorize(msgs=None)` silently over-reports — always pass real messages
- An empty phrase bank matches every string
- Zoom exports the same room's chat under two different filenames across versions (`meeting_saved_chat.txt`, `meeting_saved_new_chat.txt`) — match on content, not name
- Excel mangles long phone numbers into floats (`9.18149E+11`) and pads with zeros; `contact_norm.digits()` already refuses these — don't "fix" it to accept them
- Timestamps crossing midnight need the +24h wrap
