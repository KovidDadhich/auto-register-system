# PROJECT_ARCHITECTURE_SUMMARY.md
# Online Auto Register System — Advocate Office, Revenue Board of Rajasthan, Ajmer

> **Purpose of this file:** AI context document. Load this before any coding session to skip full repository analysis. Read only relevant module files after understanding this summary.

---

## 1. What This System Does

Automates court hearing tracking for an advocate office. Scrapes a government portal (GCMS — `gcms.rajasthan.gov.in`) daily to get bench details and next hearing dates, stores everything in SQLite, and syncs to Google Sheets as a live online register.

**Core loop:** Every hearing cycle → scrape bench info before the hearing → scrape next hearing date after the hearing → record both → repeat forever.

---

## 2. Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3 |
| Scraping | `aiohttp` (async), `BeautifulSoup` |
| Database | SQLite (`sqlite3`) |
| Scheduling | APScheduler (server) / Windows Task Scheduler (PC) |
| Sheets | `gspread` + Google Service Account |
| Config | `python-dotenv` |

---

## 3. Project Structure

```
main.py                          ← Entry point, CLI flags
config/
  settings.py                    ← All env vars and constants
  .env                           ← Secrets (gitignored)
  bench_mapping.json             ← Hindi → English bench name map
  service_account.json           ← Google auth (gitignored)
backend/
  logger.py                      ← Centralized logging (rotating file + console)
  scheduler/
    scheduler.py                 ← Pipeline orchestration + APScheduler
  queue/
    queue_builder.py             ← Creates today's FetchQueue tasks
    retry_handler.py             ← Re-queues today's failed tasks
  catchUp/
    catchUpScanner.py            ← Recovers missed tasks after shutdown
  scraper/
    task_processor.py            ← Async scraper engine (ACTIVE)
    gcms_scraper.py              ← Old sync scraper prototype (DEAD CODE)
  db/
    db_writer.py                 ← All DB write operations
  mapper/
    hindi_mapper.py              ← Bench name translation
  sheets_sync/
    sheets_sync.py               ← Google Sheets incremental sync
database/
  register.db                    ← SQLite database (gitignored)
scripts/
  create_database.py             ← One-time DB setup
  inser_test_data.py             ← Interactive test data entry
  setup_sheet.py                 ← One-time Google Sheets setup
  apply_borders.py               ← Formatting utility for existing sheet
  case_history_apps_script.js    ← Google Apps Script for Case_History_Fetch sheet
docs/
  CURRENT_TASK.md                ← Active development goal
  PROJECT_CONTEXT.md             ← Background
  PROJECT_MAP.md                 ← Module map (may be outdated)
```

---

## 4. Database Schema

### Cases (static, one row per case)
```
case_pk              INTEGER PK AUTOINCREMENT
case_id              TEXT UNIQUE    ← GCMS case ID (e.g. "2009/7852")
case_name, district, tehsil, old_case_id
connected_prakaran, prakaran, adhiniyam
to_be_continued, client_in_contact
```

### Hearings (dynamic, one row per case per hearing cycle)
```
hearing_id           INTEGER PK AUTOINCREMENT
case_pk              FK → Cases
prev_hearing_date    DATE
current_hearing_date DATE
bench_fetch_at       DATE    ← when to run fetch_bench task (= current - 1)
next_date_fetch_at   DATE    ← when to run fetch_next_date task (= current + 2)
bench_name, bench_number, bench_member, status
comments                     ← written by user in Google Sheets, synced back to DB
next_hearing_date    DATE
hearing_date_changed TEXT    ← "date_changed" if GCMS showed different date
bench_fetched        INTEGER DEFAULT 0   ← 1 = done
next_date_fetched    INTEGER DEFAULT 0   ← 1 = done
system_note          TEXT    ← catch-up anomaly messages
updated_at           TEXT    ← auto-updated by trigger on every UPDATE
last_synced_at       TEXT    ← set by sheets_sync after push
```

### FetchQueue (task queue)
```
task_id              INTEGER PK AUTOINCREMENT
case_pk              FK → Cases
hearing_id           FK → Hearings
task_type            TEXT    ← "fetch_bench" | "fetch_next_date"
scheduled_date       DATE
status               TEXT    ← "pending" | "processing" | "completed" | "failed"
retry_count          INTEGER DEFAULT 0
last_error           TEXT    ← error message OR "late_fetch" signal (see §8)
created_at           TEXT
```

**DB Trigger:** `trg_hearings_updated_at` — auto-sets `updated_at = CURRENT_TIMESTAMP` on every Hearings UPDATE. Used by sheets_sync to detect changed rows.

---

## 5. Execution Flow

### Entry Points (`main.py` CLI flags)
```
--scheduler   Server mode. APScheduler runs 24/7. Never exits.
--main        Run catch-up scanner + main pipeline, then exit.
--retry       Run catch-up scanner + retry pipeline, then exit.
--now         Run catch-up scanner + both pipelines, then exit.
```

### Main Pipeline (daily, runs at MAIN_RUN_TIME)
```
Step 0: CatchUp Scanner   → find and recover missed tasks from shutdown
Step 1: Queue Builder     → create today's FetchQueue tasks from Hearings
Step 2: Task Processor    → scrape GCMS, parse, write to DB
Step 3: Sheets Sync       → push changed DB rows to Google Sheets
                            + pull comments from sheet back to DB
```

### Retry Pipeline (daily, runs at RETRY_RUN_TIME, a few hours after main)
```
Step 1: Retry Handler     → re-queue today's failed tasks
Step 2: Task Processor    → scrape GCMS for re-queued tasks
```

---

## 6. Hearings Lifecycle (Self-Perpetuating Cycle)

This is the most important data flow to understand. Each successful scrape seeds the next cycle automatically.

```
1. Case inserted manually
   → one Hearings row created
     current_hearing_date = upcoming hearing date
     bench_fetch_at       = current_hearing_date - 1 day
     next_date_fetch_at   = current_hearing_date + 2 days

2. On bench_fetch_at date:
   → fetch_bench task runs
   → writes bench_name, bench_number, bench_member, status
   → if GCMS date ≠ stored date:
       mark old row as "date_changed"
       insert corrected Hearings row with new scheduling dates

3. On next_date_fetch_at date:
   → fetch_next_date task runs
   → writes next_hearing_date on current row
   → inserts NEW Hearings row:
       prev  = old current_hearing_date
       current = next_hearing_date (just fetched)
       new bench_fetch_at and next_date_fetch_at computed

4. → Repeat from step 2 indefinitely
```

**Important:** The system never stops on its own. It generates future work as part of processing current work.

---

## 7. Scraper Flow (`task_processor.py`)

```
_process_tasks(task_type)
  → _load_queued_tasks()         load all "pending" tasks from FetchQueue
  → split into BATCH_SIZE chunks
  → for each batch:
      asyncio.gather(*[_handle_task(...) for task in batch])
      random delay between batches (BATCH_DELAY_MIN to BATCH_DELAY_MAX seconds)

_handle_task(task)
  → mark_task_processing()
  → get_current_hearing_date()   load stored date for validation
  → _initialize_session()        GET GCMS page, extract viewstate + CSRF
  → _scrape_gcms()               POST search payload, extract UpdatePanel HTML
  → _parse_response()            extract fields by Hindi label from HTML table
  → (if fetch_next_date) check date_not_updated → reschedule or proceed
  → map_parsed_data()            translate bench_name Hindi → English
  → write_next_date() or write_bench_details()
  → mark_task_complete()
```

**Concurrency:** `asyncio.Semaphore(SEMAPHORE_LIMIT=3)` — max 3 concurrent requests.
**Each task gets its own aiohttp session** (own viewstate + CSRF token).
**GCMS response format:** ASP.NET UpdatePanel — response is pipe-delimited text, HTML is extracted between `"updatePanel"` tokens.

---

## 8. Known Complexity Areas and Design Decisions

### A. `last_error` Field Dual-Purpose (Fragile Coupling)
The catch-up scanner writes `"late_fetch"` into `last_error` to signal to `task_processor` that a `system_note` should be added to the Hearings record after writing. This repurposes an error message field as an inter-module communication channel. It works but is non-obvious. Both modules must use the exact same string.

### B. `date_not_updated` Rescheduling
When GCMS still shows the old hearing date after the hearing (not yet updated), the scraper marks the task as failed with `last_error = "date_not_updated"` and calls `reschedule_next_date_fetch()` which pushes `next_date_fetch_at` forward by 2 days. This is normal behavior — GCMS takes 1-4 days to update. This is NOT an error, it is a deliberate retry mechanism.

### C. Catch-Up Scanner Four-Case Logic
```
Case 1.1: missed fetch_bench, hearing not yet passed → re-queue normally
Case 1.2: missed fetch_bench, hearing already passed → mark unrecoverable
          (writes "N/A - system shutdown" to bench fields)
Case 2.1: missed fetch_next_date, within NEXT_DATE_GRACE_DAYS → re-queue normally
Case 2.2: missed fetch_next_date, past grace period → re-queue with late_fetch flag
          (system_note = "possible missed hearing due to shutdown")
```
Iterates in a while loop because processing next_date tasks creates new Hearings rows which may themselves need catch-up.

### D. The Selective Scraping Architecture (BEING REPLACED)
All complexity in queue_builder, catch-up scanner, and retry_handler exists because the system selectively picks which cases to scrape on which specific days. **`CURRENT_TASK.md` documents a planned migration to scraping ALL cases every day**, which will eliminate most of this complexity.

### E. write_bench_details Date-Change Handling
If GCMS shows a different hearing date than what is stored (date was rescheduled):
- Old record → marked `hearing_date_changed = "date_changed"`, `next_hearing_date = NULL`, `next_date_fetch_at = NULL`
- New record → inserted with corrected `current_hearing_date`, carrying forward `prev_hearing_date` from old record (the old current date never happened in court)

---

## 9. Google Sheets Architecture

### Sheet Structure (setup once by `setup_sheet.py`)
```
Master Sheet
  Row 1: Merged date headers (8 cols per date) — 365 dates, Dec→Jan descending
  Row 2: Sub-headers (Prev Hearing Date, Current Hearing Date, Bench Name,
          Bench Number, Bench Member, Status, Comments, Next Hearing Date)
  Row 3+: One row per case, sorted by case_id
  Cols A-K: Static case info (11 cols)
  Cols L+: Dynamic per-date data (8 cols × 365 dates)

Daily_Causelist_Fetch
  User types DD/MM/YYYY in B1 → FILTER formula shows all cases with that hearing date

Case_History_Fetch
  User types Case ID in B1 → Apps Script fetches full hearing history from Master Sheet
```

### Sync Logic (`sheets_sync.py`)
**Outbound (DB → Sheet):** Queries `WHERE updated_at > last_synced_at`. Writes only changed cells. Never writes the Comments column. Sets `last_synced_at` after writing.

**Inbound (Sheet → DB):** Reads Comments column for every case × date. Updates DB only when sheet value differs from DB value.

**New case rows:** Inserted in sorted position by `case_id`. All S.No. values are renumbered after insert.

---

## 10. Configuration Constants (settings.py / .env)

| Constant | Purpose |
|---|---|
| `DB_PATH` | SQLite file path |
| `URL` | GCMS portal URL |
| `MAX_RETRIES` | Max task retry attempts (default 5) |
| `SCRAPER_TIMEOUT` | aiohttp request timeout in seconds |
| `BATCH_SIZE` | Tasks per batch in task_processor |
| `BATCH_DELAY_MIN/MAX` | Random delay range between batches (seconds) |
| `MAIN_RUN_TIME` | HH:MM for main pipeline (APScheduler mode) |
| `RETRY_RUN_TIME` | HH:MM for retry pipeline (APScheduler mode) |
| `NEXT_DATE_GRACE_DAYS` | Days after hearing before next_date fetch is "late" |
| `BENCH_CUTOFF_HOUR` | Hour after which bench data is unrecoverable |
| `DATE_CUTOFF_HOUR` | Hour after which next_date is considered late |
| `MAPPING_FILE` | Path to bench_mapping.json |
| `SERVICE_ACCOUNT_FILE` | Path to Google service account JSON |
| `SHEET_ID_{YEAR}` | Google Sheet ID per year (e.g. SHEET_ID_2026) |

---

## 11. Important Rules and Invariants

- `case_pk` is always auto-incremented. Never manually set.
- `case_id` is the GCMS identifier (e.g. `"2009/7852"`). Must be unique.
- One Hearings row per case per hearing cycle. Not one per case.
- `bench_fetch_at` is always `current_hearing_date - 1`.
- `next_date_fetch_at` is always `current_hearing_date + 2`.
- GCMS dates come in `DD/MM/YYYY` format. DB stores `YYYY-MM-DD`. All conversions go through `_parse_gcms_date()` in `db_writer.py`.
- The Comments column in Google Sheets is user-owned. Sheets sync never overwrites it outbound.
- `updated_at` on Hearings is auto-managed by DB trigger. Never set manually.
- `gcms_scraper.py` is dead code. Do not use or modify it.

---

## 12. Current Development Goal (`CURRENT_TASK.md`)

**Migrating from selective-case to full-database daily scraping.**

Planned removals upon migration:
- `catchUpScanner.py` — entire module
- `gcms_scraper.py` — already dead
- `queue_builder.py` — replace with ~15-line version
- `retry_handler.py` — simplify significantly
- Hearings columns: `bench_fetch_at`, `next_date_fetch_at`, `bench_fetched`, `next_date_fetched`
- Settings: `BENCH_CUTOFF_HOUR`, `DATE_CUTOFF_HOUR`, `NEXT_DATE_GRACE_DAYS`
- The `fetch_bench` / `fetch_next_date` task type split → one unified task type

What stays: `task_processor.py`, `db_writer.py`, `sheets_sync.py`, `hindi_mapper.py`, `logger.py`, `scheduler.py` (simplified), `setup_sheet.py`, `create_database.py` (updated schema).

---

## 13. Module Dependency Map

```
main.py
  └── scheduler.py
        ├── queue_builder.py  ──→  db_writer.get_conn()
        ├── retry_handler.py  ──→  settings.py
        ├── task_processor.py ──→  db_writer (all write fns)
        │                     ──→  hindi_mapper.py
        │                     ──→  logger.py
        ├── catchUpScanner.py ──→  db_writer.get_conn()
        └── sheets_sync.py    ──→  db_writer.get_conn()

All modules → config/settings.py
All modules → backend/logger.py
```

---

*Last updated: May 2026 | System version: selective-case scraping (pre-migration)*
