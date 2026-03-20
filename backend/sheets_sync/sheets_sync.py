'''
Purpose: Google Sheets incremental sync for the Register System.

Requires: setup_sheet.py to have been run first to create all 365 date headers.

Outbound: Pushes only new/changed hearing rows from DB → Master Sheet.
  - Date columns already exist (pre-created by setup_sheet.py)
  - New cases → inserts new row in correct sorted position
  - Updated data → writes to correct cells (never touches comments column)
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
import time
from datetime import datetime, date
from google.oauth2.service_account import Credentials

from config.settings import SERVICE_ACCOUNT_FILE
from backend.logger import get_logger
from backend.db.db_writer import get_conn

logger = get_logger(__name__)

SCOPES              = ["https://www.googleapis.com/auth/spreadsheets"]
STATIC_COLS         = 7
DYNAMIC_COLS        = 8
HEADER_ROW          = 1
SUBHEADER_ROW       = 2
DATA_START_ROW      = 3
STATIC_HEADERS      = ["S. No.", "Case ID", "Case Name", "District", "Prakaran", "Adhiniyam", "Old Case ID"]
DYNAMIC_HEADERS     = ["Prev Hearing Date", "Current Hearing Date", "Bench Name", "Bench Number", "Bench Member", "Status", "Comments", "Next Hearing Date"]
COMMENTS_COL_OFFSET = 6
WORKSHEET_NAME      = "Master Sheet"
API_DELAY_SECONDS   = 1.5
CELL_WRITE_CHUNK    = 500


def run_sheets_sync():
    logger.info("Sheets sync started.")
    current_year = date.today().year
    sheet_id     = _get_sheet_id(current_year)
    if not sheet_id:
        logger.error(f"No sheet ID configured for year {current_year}. Set SHEET_ID_{current_year} in .env")
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


def update_master_sheet(worksheet: gspread.Worksheet, year: int):
    """
    Incremental outbound sync.
    Date columns are pre-created by setup_sheet.py — no column insertion here.
    Only inserts new case rows if needed, then writes cell data.
    """
    logger.info(f"Outbound incremental sync started for year {year}.")

    sheet_state = _read_sheet_state(worksheet)
    unsynced    = _fetch_unsynced_hearings(year)

    if not unsynced:
        logger.info("No unsynced hearings found. Outbound sync complete.")
        return

    logger.info(f"Found {len(unsynced)} unsynced hearing(s).")
    now_str = datetime.now().isoformat(sep=" ", timespec="seconds")

    # Insert new case rows if any
    new_cases        = sorted(set(h["case_id"] for h in unsynced if h["case_id"] not in sheet_state["case_to_row"]))
    case_data_lookup = {h["case_id"]: h for h in unsynced}
    for case_id in new_cases:
        if case_id in case_data_lookup:
            _insert_case_row(worksheet, sheet_state, case_data_lookup[case_id])
            time.sleep(API_DELAY_SECONDS)

    # Build all cell updates
    all_cell_updates   = []
    synced_hearing_ids = []

    for hearing in unsynced:
        hearing_date = hearing["current_hearing_date"]
        case_id      = hearing["case_id"]

        if hearing_date not in sheet_state["date_to_col"]:
            logger.warning(
                f"Date {hearing_date} not found in sheet — "
                f"run setup_sheet.py {year} first. Skipping hearing_id={hearing['hearing_id']}."
            )
            continue

        if case_id not in sheet_state["case_to_row"]:
            logger.warning(f"Case {case_id} not in sheet after insert. Skipping.")
            continue

        date_col_start = sheet_state["date_to_col"][hearing_date]
        data_row       = sheet_state["case_to_row"][case_id]

        fields = [
            _fmt_date(hearing["prev_hearing_date"]),
            _fmt_date(hearing["current_hearing_date"]),
            hearing["bench_name"]   or "",
            hearing["bench_number"] or "",
            hearing["bench_member"] or "",
            hearing["status"]       or "",
            None,   # Comments — never overwrite
            _fmt_date(hearing["next_hearing_date"]),
        ]

        for offset, value in enumerate(fields):
            if value is None:
                continue
            all_cell_updates.append(
                gspread.Cell(row=data_row, col=date_col_start + offset + 1, value=value)
            )
        synced_hearing_ids.append(hearing["hearing_id"])

    if all_cell_updates:
        _batch_update_cells(worksheet, all_cell_updates)

    if synced_hearing_ids:
        _mark_synced(synced_hearing_ids, now_str)
        logger.info(f"Outbound sync complete. {len(synced_hearing_ids)} hearing(s) synced.")


def _batch_update_cells(worksheet: gspread.Worksheet, cells: list):
    for i in range(0, len(cells), CELL_WRITE_CHUNK):
        chunk = cells[i:i + CELL_WRITE_CHUNK]
        worksheet.update_cells(chunk)
        logger.debug(f"Wrote chunk of {len(chunk)} cells.")
        if i + CELL_WRITE_CHUNK < len(cells):
            time.sleep(API_DELAY_SECONDS)


def _insert_case_row(worksheet: gspread.Worksheet, sheet_state: dict, hearing: sqlite3.Row):
    """Inserts a new case row in correct sorted position."""
    case_id   = hearing["case_id"]
    all_cases = sorted(list(sheet_state["case_to_row"].keys()) + [case_id])
    insert_pos = all_cases.index(case_id)
    insert_row = DATA_START_ROW + insert_pos

    for cid in sheet_state["case_to_row"]:
        if sheet_state["case_to_row"][cid] >= insert_row:
            sheet_state["case_to_row"][cid] += 1

    worksheet.spreadsheet.batch_update({"requests": [{
        "insertDimension": {
            "range": {
                "sheetId"   : worksheet.id,
                "dimension" : "ROWS",
                "startIndex": insert_row - 1,
                "endIndex"  : insert_row,
            },
            "inheritFromBefore": False,
        }
    }]})
    time.sleep(API_DELAY_SECONDS)

    worksheet.update(f"A{insert_row}:G{insert_row}", [[
        insert_pos + 1,
        hearing["case_id"],
        hearing["case_name"]   or "",
        hearing["district"]    or "",
        hearing["prakaran"]    or "",
        hearing["adhiniyam"]   or "",
        hearing["old_case_id"] or "",
    ]])

    sheet_state["case_to_row"][case_id] = insert_row
    _renumber_sno(worksheet, sheet_state)
    logger.info(f"Inserted case row for case_id={case_id} at row {insert_row}.")


def _renumber_sno(worksheet: gspread.Worksheet, sheet_state: dict):
    updates = []
    for cid, row in sorted(sheet_state["case_to_row"].items(), key=lambda x: x[1]):
        sno = row - DATA_START_ROW + 1
        updates.append(gspread.Cell(row=row, col=1, value=sno))
    if updates:
        worksheet.update_cells(updates)
        time.sleep(API_DELAY_SECONDS)


def sync_comments_to_db(worksheet: gspread.Worksheet, year: int):
    logger.info("Inbound sync (comments) started.")
    all_values = worksheet.get_all_values()
    if len(all_values) < DATA_START_ROW:
        logger.warning("Sheet has no data rows. Skipping inbound sync.")
        return

    date_columns = _parse_date_columns(all_values[HEADER_ROW - 1])
    if not date_columns:
        logger.warning("No date columns found. Skipping inbound sync.")
        return

    case_lookup = _fetch_case_id_to_pk_map()
    updated     = 0

    for row in all_values[DATA_START_ROW - 1:]:
        if not row or len(row) < 2:
            continue
        case_id = row[1].strip()
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

    logger.info(f"Inbound sync complete. {updated} comment(s) updated in DB.")


def _read_sheet_state(worksheet: gspread.Worksheet) -> dict:
    all_values  = worksheet.get_all_values()
    date_to_col = {}
    case_to_row = {}
    dates_sorted = []

    if not all_values:
        return {"date_to_col": date_to_col, "case_to_row": case_to_row, "dates_sorted": dates_sorted}

    for col_idx, cell in enumerate(all_values[0]):
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

    if len(all_values) >= DATA_START_ROW:
        for row_idx, row in enumerate(all_values[DATA_START_ROW - 1:], start=DATA_START_ROW):
            if len(row) > 1 and row[1].strip():
                case_to_row[row[1].strip()] = row_idx

    return {"date_to_col": date_to_col, "case_to_row": case_to_row, "dates_sorted": dates_sorted}


def _fetch_unsynced_hearings(year: int) -> list:
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT h.hearing_id, h.case_pk,
                   h.prev_hearing_date, h.current_hearing_date,
                   h.bench_name, h.bench_number, h.bench_member,
                   h.status, h.comments, h.next_hearing_date,
                   h.updated_at, h.last_synced_at,
                   c.case_id, c.case_name, c.district,
                   c.prakaran, c.adhiniyam, c.old_case_id
            FROM Hearings h
            JOIN Cases c ON h.case_pk = c.case_pk
            WHERE strftime('%Y', h.current_hearing_date) = ?
              AND (
                h.last_synced_at IS NULL
                OR h.updated_at  IS NULL
                OR h.updated_at  > h.last_synced_at
              )
            ORDER BY c.case_id ASC, h.current_hearing_date DESC
            """,
            (str(year),),
        ).fetchall()
    finally:
        conn.close()


def _mark_synced(hearing_ids: list, now_str: str):
    conn = get_conn()
    try:
        placeholders = ",".join("?" * len(hearing_ids))
        conn.execute(
            f"UPDATE Hearings SET last_synced_at = ? WHERE hearing_id IN ({placeholders})",
            [now_str] + hearing_ids,
        )
        conn.commit()
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


def _get_hearing_id(case_pk: int, current_hearing_date: str):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT hearing_id FROM Hearings WHERE case_pk = ? AND current_hearing_date = ?",
            (case_pk, current_hearing_date),
        ).fetchone()
        return row["hearing_id"] if row else None
    finally:
        conn.close()


def _get_db_comment(hearing_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT comments FROM Hearings WHERE hearing_id = ?", (hearing_id,)).fetchone()
        return row["comments"] if row else None
    finally:
        conn.close()


def _update_comment_in_db(hearing_id: int, comment: str):
    conn = get_conn()
    try:
        conn.execute("UPDATE Hearings SET comments = ? WHERE hearing_id = ?", (comment or None, hearing_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to update comment | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()


def _authenticate() -> gspread.Client:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet_id(year: int):
    return os.getenv(f"SHEET_ID_{year}")


def _get_or_create_worksheet(workbook, name: str) -> gspread.Worksheet:
    try:
        return workbook.worksheet(name)
    except gspread.WorksheetNotFound:
        logger.info(f"Worksheet '{name}' not found. Creating it.")
        return workbook.add_worksheet(title=name, rows=3000, cols=500)


def _parse_date_columns(header_row: list) -> list:
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


def _fmt_date(date_str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return date_str or ""


def _col_letter(col_1based: int) -> str:
    result = ""
    while col_1based > 0:
        col_1based, remainder = divmod(col_1based - 1, 26)
        result = chr(65 + remainder) + result
    return result