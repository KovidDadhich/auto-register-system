'''
Purpose: Google Sheets sync for the Register System.

Outbound (incremental): Pushes only new/changed hearing rows from DB → Master Sheet.
  - Detects changes via updated_at > last_synced_at
  - New dates → inserts 8 columns at position H, shifts existing right
  - New cases → inserts new row in correct sorted position
  - Updated data → overwrites specific cells (never touches comments column)
  - After sync → sets last_synced_at = now on synced rows

Inbound: Reads comments from Master Sheet → saves to DB.
  - Only reads comments column per case per date
  - Never overwrites any other column

Master Sheet structure:
  Row 1 : Merged date headers (8 cols per date), newest date at col H
  Row 2 : Column headers (static + dynamic)
  Row 3+: One row per case, sorted by case_id

Static columns (A-G): S.No., Case ID, Case Name, District, Prakaran, Adhiniyam, Old Case ID
Dynamic columns per date (8):
  Prev Hearing Date, Current Hearing Date, Bench Name, Bench Number,
  Bench Member, Status, Comments, Next Hearing Date

Auth: Google Service Account JSON key file.
'''

import sqlite3
import gspread
import os
from datetime import datetime, date
from google.oauth2.service_account import Credentials

from config.settings import DB_PATH, SERVICE_ACCOUNT_FILE
from backend.logger import get_logger
from backend.db.db_writer import get_conn

logger = get_logger(__name__)

# ── Google Sheets API scopes ───────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# ── Sheet structure constants ──────────────────────────────────────────────────
STATIC_COLS        = 7
DYNAMIC_COLS       = 8
HEADER_ROW         = 1
SUBHEADER_ROW      = 2
DATA_START_ROW     = 3
STATIC_HEADERS     = ["S. No.", "Case ID", "Case Name", "District", "Prakaran", "Adhiniyam", "Old Case ID"]
DYNAMIC_HEADERS    = ["Prev Hearing Date", "Current Hearing Date", "Bench Name", "Bench Number", "Bench Member", "Status", "Comments", "Next Hearing Date"]
COMMENTS_COL_OFFSET = 6   # 0-based offset within dynamic headers (Comments = 7th col)
WORKSHEET_NAME     = "Master Sheet"


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run_sheets_sync():
    """
    Main entry point called by scheduler.
    Runs outbound incremental sync then inbound comments sync.
    """
    logger.info("Sheets sync started.")

    current_year = date.today().year
    sheet_id     = _get_sheet_id(current_year)

    if not sheet_id:
        logger.error(
            f"No sheet ID configured for year {current_year}. "
            f"Set SHEET_ID_{current_year} in .env"
        )
        return

    try:
        gc        = _authenticate()
        workbook  = gc.open_by_key(sheet_id)
        worksheet = _get_or_create_worksheet(workbook, WORKSHEET_NAME)

        update_master_sheet(worksheet, current_year)
        sync_comments_to_db(worksheet, current_year)

        logger.info("Sheets sync completed successfully.")

    except Exception as e:
        logger.error(f"Sheets sync failed: {e}")
        raise


# ══════════════════════════════════════════════════════════════════════════════
# OUTBOUND SYNC — incremental DB → Sheet
# ══════════════════════════════════════════════════════════════════════════════

def update_master_sheet(worksheet: gspread.Worksheet, year: int):
    """
    Incremental outbound sync.

    Steps:
    1. Read current sheet state (headers, existing dates, existing case rows)
    2. Fetch unsynced hearings from DB (updated_at > last_synced_at OR never synced)
    3. For each unsynced hearing:
       a. If date not in sheet → insert 8 new columns at H, write headers
       b. If case not in sheet → insert new row in sorted position
       c. Write hearing data into correct cells (skip comments column)
    4. Mark synced rows in DB (last_synced_at = now)
    """
    logger.info(f"Outbound incremental sync started for year {year}.")

    # ── Step 1: Read current sheet state ──────────────────────────────────────
    sheet_state = _read_sheet_state(worksheet)

    # ── Step 2: Fetch unsynced hearings from DB ───────────────────────────────
    unsynced = _fetch_unsynced_hearings(year)

    if not unsynced:
        logger.info("No unsynced hearings found. Outbound sync complete.")
        return

    logger.info(f"Found {len(unsynced)} unsynced hearing(s).")

    synced_hearing_ids = []
    now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

    for hearing in unsynced:
        try:
            _sync_one_hearing(worksheet, sheet_state, hearing)
            synced_hearing_ids.append(hearing["hearing_id"])
        except Exception as e:
            logger.error(
                f"Failed to sync hearing_id={hearing['hearing_id']} | error={e}"
            )
            # Continue with other hearings — don't stop entire sync

    # ── Step 4: Mark synced rows in DB ───────────────────────────────────────
    if synced_hearing_ids:
        _mark_synced(synced_hearing_ids, now_str)
        logger.info(f"Outbound sync complete. {len(synced_hearing_ids)} hearing(s) synced.")


def _sync_one_hearing(
    worksheet   : gspread.Worksheet,
    sheet_state : dict,
    hearing     : sqlite3.Row,
):
    """
    Syncs a single hearing row to the sheet.
    Handles: new date column, new case row, data update.
    """
    hearing_date = hearing["current_hearing_date"]
    case_id      = hearing["case_id"]

    # ── Ensure date column exists in sheet ────────────────────────────────────
    if hearing_date not in sheet_state["date_to_col"]:
        _insert_date_columns(worksheet, sheet_state, hearing_date)

    date_col_start = sheet_state["date_to_col"][hearing_date]
    comments_col   = date_col_start + COMMENTS_COL_OFFSET

    # ── Ensure case row exists in sheet ───────────────────────────────────────
    if case_id not in sheet_state["case_to_row"]:
        _insert_case_row(worksheet, sheet_state, hearing)

    data_row = sheet_state["case_to_row"][case_id]

    # ── Write hearing data (skip comments column) ─────────────────────────────
    cell_updates = []

    fields = [
        _fmt_date(hearing["prev_hearing_date"]),
        _fmt_date(hearing["current_hearing_date"]),
        hearing["bench_name"]   or "",
        hearing["bench_number"] or "",
        hearing["bench_member"] or "",
        hearing["status"]       or "",
        None,                              # Comments — skip, never overwrite
        _fmt_date(hearing["next_hearing_date"]),
    ]

    for offset, value in enumerate(fields):
        if value is None:
            continue   # skip comments column
        col = date_col_start + offset
        cell_updates.append(
            gspread.Cell(row=data_row, col=col + 1, value=value)
        )

    if cell_updates:
        worksheet.update_cells(cell_updates)
        logger.debug(
            f"Synced | case_id={case_id} | date={hearing_date} | "
            f"row={data_row} | col_start={date_col_start + 1}"
        )


def _insert_date_columns(
    worksheet    : gspread.Worksheet,
    sheet_state  : dict,
    hearing_date : str,
):
    """
    Inserts 8 new columns at position H (col index STATIC_COLS, 0-based).
    Newest date always goes at H, pushing older dates right.
    Writes merged header and subheader for the new date.
    Updates sheet_state to reflect new column positions.
    """
    insert_at_col = STATIC_COLS + 1   # 1-based, column H

    # Shift all existing date col positions right by DYNAMIC_COLS
    for d in sheet_state["date_to_col"]:
        sheet_state["date_to_col"][d] += DYNAMIC_COLS

    # Insert 8 blank columns at H
    worksheet.spreadsheet.batch_update({
        "requests": [{
            "insertDimension": {
                "range": {
                    "sheetId"    : worksheet.id,
                    "dimension"  : "COLUMNS",
                    "startIndex" : STATIC_COLS,
                    "endIndex"   : STATIC_COLS + DYNAMIC_COLS,
                },
                "inheritFromBefore": False,
            }
        }]
    })

    # Write merged date header in row 1
    date_label = _format_date_header(hearing_date)
    worksheet.update_cell(HEADER_ROW, insert_at_col, date_label)

    # Merge row 1 cells for this date group
    worksheet.spreadsheet.batch_update({
        "requests": [_merge_request(
            sheet_id  = worksheet.id,
            row       = 0,
            col_start = STATIC_COLS,
            col_end   = STATIC_COLS + DYNAMIC_COLS - 1,
        )]
    })

    # Write subheaders in row 2
    subheader_range = f"{_col_letter(insert_at_col)}{SUBHEADER_ROW}:{_col_letter(insert_at_col + DYNAMIC_COLS - 1)}{SUBHEADER_ROW}"
    worksheet.update(subheader_range, [DYNAMIC_HEADERS])

    # Register new date in sheet_state (0-based col index)
    sheet_state["date_to_col"][hearing_date] = STATIC_COLS
    sheet_state["dates_sorted"].insert(0, hearing_date)

    logger.info(f"Inserted new date columns for {hearing_date} at col H.")


def _insert_case_row(
    worksheet   : gspread.Worksheet,
    sheet_state : dict,
    hearing     : sqlite3.Row,
):
    """
    Inserts a new case row in the correct sorted position (by case_id).
    Writes static columns for the new case.
    Updates sheet_state.
    """
    case_id  = hearing["case_id"]
    all_cases = sorted(list(sheet_state["case_to_row"].keys()) + [case_id])
    insert_pos = all_cases.index(case_id)
    insert_row = DATA_START_ROW + insert_pos   # 1-based sheet row

    # Shift all existing row positions down by 1
    for cid in sheet_state["case_to_row"]:
        if sheet_state["case_to_row"][cid] >= insert_row:
            sheet_state["case_to_row"][cid] += 1

    # Insert blank row
    worksheet.spreadsheet.batch_update({
        "requests": [{
            "insertDimension": {
                "range": {
                    "sheetId"    : worksheet.id,
                    "dimension"  : "ROWS",
                    "startIndex" : insert_row - 1,
                    "endIndex"   : insert_row,
                },
                "inheritFromBefore": False,
            }
        }]
    })

    # Write static columns
    sno = insert_pos + 1
    static_values = [[
        sno,
        hearing["case_id"],
        hearing["case_name"]   or "",
        hearing["district"]    or "",
        hearing["prakaran"]    or "",
        hearing["adhiniyam"]   or "",
        hearing["old_case_id"] or "",
    ]]
    worksheet.update(f"A{insert_row}:G{insert_row}", static_values)

    # Register in sheet_state
    sheet_state["case_to_row"][case_id] = insert_row

    # Renumber S.No. for all rows after insert
    _renumber_sno(worksheet, sheet_state)

    logger.info(f"Inserted new case row for case_id={case_id} at row {insert_row}.")


def _renumber_sno(worksheet: gspread.Worksheet, sheet_state: dict):
    """Renumbers S.No. column (col A) for all data rows after a row insert."""
    updates = []
    for cid, row in sorted(sheet_state["case_to_row"].items(), key=lambda x: x[1]):
        sno = row - DATA_START_ROW + 1
        updates.append(gspread.Cell(row=row, col=1, value=sno))
    if updates:
        worksheet.update_cells(updates)


# ══════════════════════════════════════════════════════════════════════════════
# INBOUND SYNC — Sheet → DB (comments only)
# ══════════════════════════════════════════════════════════════════════════════

def sync_comments_to_db(worksheet: gspread.Worksheet, year: int):
    """
    Reads comments from Master Sheet and saves new/updated values to DB.
    Only reads the comments column (offset 6 within each date group).
    Never writes to the sheet.
    """
    logger.info("Inbound sync (comments) started.")

    all_values = worksheet.get_all_values()

    if len(all_values) < DATA_START_ROW:
        logger.warning("Sheet has no data rows. Skipping inbound sync.")
        return

    header_row_1 = all_values[HEADER_ROW - 1]

    # Parse date column positions from row 1
    date_columns = _parse_date_columns(header_row_1)

    if not date_columns:
        logger.warning("No date columns found in sheet header. Skipping inbound sync.")
        return

    case_lookup = _fetch_case_id_to_pk_map()
    updated     = 0

    for row in all_values[DATA_START_ROW - 1:]:
        if not row or len(row) < 2:
            continue

        case_id = row[1].strip() if len(row) > 1 else None
        if not case_id or case_id not in case_lookup:
            continue

        case_pk = case_lookup[case_id]

        for date_col_start, hearing_date_str in date_columns:
            comments_col = date_col_start + COMMENTS_COL_OFFSET

            if comments_col >= len(row):
                continue

            sheet_comment = row[comments_col].strip()
            hearing_id    = _get_hearing_id(case_pk, hearing_date_str)

            if not hearing_id:
                continue

            db_comment = _get_db_comment(hearing_id)

            if sheet_comment != (db_comment or ""):
                _update_comment_in_db(hearing_id, sheet_comment)
                updated += 1
                logger.debug(
                    f"Comment updated | case_id={case_id} | "
                    f"date={hearing_date_str} | comment='{sheet_comment}'"
                )

    logger.info(f"Inbound sync complete. {updated} comment(s) updated in DB.")


# ══════════════════════════════════════════════════════════════════════════════
# SHEET STATE READER
# ══════════════════════════════════════════════════════════════════════════════

def _read_sheet_state(worksheet: gspread.Worksheet) -> dict:
    """
    Reads current sheet and returns:
    {
        date_to_col  : { "YYYY-MM-DD": 0-based col index },
        case_to_row  : { "case_id": 1-based row number },
        dates_sorted : [ "YYYY-MM-DD", ... ] newest first
    }
    """
    all_values = worksheet.get_all_values()

    date_to_col  = {}
    case_to_row  = {}
    dates_sorted = []

    if not all_values:
        return {"date_to_col": date_to_col, "case_to_row": case_to_row, "dates_sorted": dates_sorted}

    # Parse date headers from row 1
    if len(all_values) >= 1:
        header_row_1 = all_values[0]
        for col_idx, cell in enumerate(header_row_1):
            if col_idx < STATIC_COLS or not cell.strip():
                continue
            try:
                date_part = cell.strip().split(" ")[0]
                d, m, y   = date_part.split("/")
                date_str  = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                date_to_col[date_str] = col_idx
                dates_sorted.append(date_str)
            except Exception:
                continue

    # Parse case rows from data rows
    if len(all_values) >= DATA_START_ROW:
        for row_idx, row in enumerate(all_values[DATA_START_ROW - 1:], start=DATA_START_ROW):
            if len(row) > 1 and row[1].strip():
                case_to_row[row[1].strip()] = row_idx

    return {
        "date_to_col" : date_to_col,
        "case_to_row" : case_to_row,
        "dates_sorted": dates_sorted,
    }


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_unsynced_hearings(year: int) -> list:
    """
    Fetches hearings that need syncing:
    - Never synced (last_synced_at IS NULL), OR
    - Updated since last sync (updated_at > last_synced_at)
    Only for current year.
    Joins Cases for static data.
    """
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT
                h.hearing_id,
                h.case_pk,
                h.prev_hearing_date,
                h.current_hearing_date,
                h.bench_name,
                h.bench_number,
                h.bench_member,
                h.status,
                h.comments,
                h.next_hearing_date,
                h.updated_at,
                h.last_synced_at,
                c.case_id,
                c.case_name,
                c.district,
                c.prakaran,
                c.adhiniyam,
                c.old_case_id
            FROM Hearings h
            JOIN Cases c ON h.case_pk = c.case_pk
            WHERE strftime('%Y', h.current_hearing_date) = ?
              AND (
                h.last_synced_at IS NULL
                OR h.updated_at > h.last_synced_at
              )
            ORDER BY c.case_id ASC, h.current_hearing_date DESC
            """,
            (str(year),),
        ).fetchall()
    finally:
        conn.close()


def _mark_synced(hearing_ids: list, now_str: str):
    """Sets last_synced_at = now for all successfully synced hearings."""
    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(hearing_ids))
        conn.execute(
            f"UPDATE Hearings SET last_synced_at = ? WHERE hearing_id IN ({placeholders})",
            [now_str] + hearing_ids,
        )
        conn.commit()
        logger.debug(f"Marked {len(hearing_ids)} hearings as synced.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to mark hearings as synced: {e}")
        raise
    finally:
        conn.close()


def _fetch_case_id_to_pk_map() -> dict:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT case_pk, case_id FROM Cases").fetchall()
        return {row["case_id"]: row["case_pk"] for row in rows}
    finally:
        conn.close()


def _get_hearing_id(case_pk: int, current_hearing_date: str) -> int | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT hearing_id FROM Hearings WHERE case_pk = ? AND current_hearing_date = ?",
            (case_pk, current_hearing_date),
        ).fetchone()
        return row["hearing_id"] if row else None
    finally:
        conn.close()


def _get_db_comment(hearing_id: int) -> str | None:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT comments FROM Hearings WHERE hearing_id = ?",
            (hearing_id,),
        ).fetchone()
        return row["comments"] if row else None
    finally:
        conn.close()


def _update_comment_in_db(hearing_id: int, comment: str):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE Hearings SET comments = ? WHERE hearing_id = ?",
            (comment or None, hearing_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update comment | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS AUTH + HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _authenticate() -> gspread.Client:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet_id(year: int) -> str | None:
    return os.getenv(f"SHEET_ID_{year}")


def _get_or_create_worksheet(
    workbook       : gspread.Spreadsheet,
    worksheet_name : str,
) -> gspread.Worksheet:
    try:
        return workbook.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        logger.info(f"Worksheet '{worksheet_name}' not found. Creating it.")
        return workbook.add_worksheet(title=worksheet_name, rows=3000, cols=500)


def _parse_date_columns(header_row: list) -> list:
    """
    Parses row 1 to find (col_index_0based, date_str_YYYY-MM-DD) for each date.
    """
    result = []
    for col_idx, cell in enumerate(header_row):
        if col_idx < STATIC_COLS or not cell.strip():
            continue
        try:
            date_part = cell.strip().split(" ")[0]
            d, m, y   = date_part.split("/")
            date_str  = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
            result.append((col_idx, date_str))
        except Exception:
            continue
    return result


def _merge_request(sheet_id: int, row: int, col_start: int, col_end: int) -> dict:
    return {
        "mergeCells": {
            "range": {
                "sheetId"         : sheet_id,
                "startRowIndex"   : row,
                "endRowIndex"     : row + 1,
                "startColumnIndex": col_start,
                "endColumnIndex"  : col_end + 1,
            },
            "mergeType": "MERGE_ALL",
        }
    }


def _format_date_header(date_str: str) -> str:
    """YYYY-MM-DD → DD/MM/YYYY DayName"""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y %A")
    except Exception:
        return date_str


def _fmt_date(date_str: str | None) -> str:
    """YYYY-MM-DD → DD/MM/YYYY for display."""
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return date_str or ""


def _col_letter(col_1based: int) -> str:
    """Converts 1-based column number to letter(s). e.g. 1→A, 27→AA."""
    result = ""
    while col_1based > 0:
        col_1based, remainder = divmod(col_1based - 1, 26)
        result = chr(65 + remainder) + result
    return result