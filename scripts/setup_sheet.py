'''
Purpose: One-time setup script for Google Sheets Master Sheet.
Creates all 365 date headers in descending order (Dec → Jan),
subheaders, and static columns for all existing cases.

Usage:
    python scripts/setup_sheet.py 2026
    python scripts/setup_sheet.py 2027
'''

import sys
import os
import time
import gspread
from datetime import date, timedelta, datetime
from google.oauth2.service_account import Credentials

# ── Path setup ─────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, "config", ".env"))

from config.settings import SERVICE_ACCOUNT_FILE
from backend.db.db_writer import get_conn
from backend.logger import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
SCOPES            = ["https://www.googleapis.com/auth/spreadsheets"]
STATIC_COLS       = 7
DYNAMIC_COLS      = 8
STATIC_HEADERS    = ["S. No.", "Case ID", "Case Name", "District", "Prakaran", "Adhiniyam", "Old Case ID"]
DYNAMIC_HEADERS   = ["Prev Hearing Date", "Current Hearing Date", "Bench Name", "Bench Number", "Bench Member", "Status", "Comments", "Next Hearing Date"]
WORKSHEET_NAME    = "Master Sheet"
API_DELAY         = 1.5    # seconds between API calls
MERGE_BATCH_SIZE  = 200     # merge requests per batch_update call
COL_WRITE_CHUNK   = 200    # columns per row write chunk
CELL_WRITE_CHUNK  = 500    # cells per update_cells call

DAY_COLORS = {
    "Monday":    {"dark": "#fce5cd", "light": "#fff2cc"}, # Orange
    "Tuesday":   {"dark": "#d9ead3", "light": "#f1f8e9"}, # Green
    "Wednesday": {"dark": "#cfe2f3", "light": "#e8f0fe"}, # Cornflower
    "Thursday":  {"dark": "#d9d2e9", "light": "#f3e5f5"}, # Purple
    "Friday":    {"dark": "#fff2cc", "light": "#fffde7"}, # Yellow
    "Saturday":  {"dark": "#d0e0e3", "light": "#e0f7fa"}, # Cyan
    "Sunday":    {"dark": "#ead1dc", "light": "#fce4ec"}, # Magenta
}

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/setup_sheet.py <year>")
        print("Example: python scripts/setup_sheet.py 2026")
        sys.exit(1)

    try:
        year = int(sys.argv[1])
    except ValueError:
        print(f"Invalid year: {sys.argv[1]}")
        sys.exit(1)

    sheet_id = os.getenv(f"SHEET_ID_{year}")
    if not sheet_id:
        print(f"Error: SHEET_ID_{year} not set in .env")
        sys.exit(1)

    # ── Authenticate ──────────────────────────────────────────────────────────
    creds     = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc        = gspread.authorize(creds)
    workbook  = gc.open_by_key(sheet_id)
    worksheet = _get_or_create_worksheet(workbook, WORKSHEET_NAME)

    # ── Confirm ───────────────────────────────────────────────────────────────
    print(f"\nThis will CLEAR and REBUILD the '{WORKSHEET_NAME}' sheet for year {year}.")
    print(f"Workbook: {workbook.title}")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    # ── Generate all dates Dec 31 → Jan 1 ────────────────────────────────────
    all_dates  = _generate_dates_descending(year)
    total_cols = STATIC_COLS + (len(all_dates) * DYNAMIC_COLS)
    cases      = _fetch_cases()
    total_rows = 2 + len(cases) + 10   # 2 header rows + cases + buffer

    logger.info(f"Year {year} | {len(all_dates)} dates | {total_cols} columns | {len(cases)} cases")

    # ── Step 1: Resize sheet ──────────────────────────────────────────────────
    logger.info("Step 1/5 | Resizing sheet...")
    worksheet.spreadsheet.batch_update({"requests": [{
        "updateSheetProperties": {
            "properties": {
                "sheetId"        : worksheet.id,
                "gridProperties" : {
                    "rowCount"   : total_rows,
                    "columnCount": total_cols,
                }
            },
            "fields": "gridProperties(rowCount,columnCount)"
        }
    }]})
    time.sleep(API_DELAY)

    # ── Step 2: Clear existing content ───────────────────────────────────────
    logger.info("Step 2/5 | Clearing existing content...")
    worksheet.clear()
    time.sleep(API_DELAY)

    # ── Step 3: Write row 1 — date headers ───────────────────────────────────
    logger.info("Step 3/5 | Writing date headers (row 1)...")
    row1 = ["Static Details"] + [""] * (STATIC_COLS - 1)
    for d in all_dates:
        try:
            dt    = datetime.strptime(d, "%Y-%m-%d")
            label = dt.strftime("%d/%m/%Y %A")
        except Exception:
            label = d
        row1.append(label)
        row1.extend([""] * (DYNAMIC_COLS - 1))
    _write_row_chunked(worksheet, row_number=1, values=row1)

    # ── Step 4: Write row 2 — subheaders ─────────────────────────────────────
    logger.info("Step 4/5 | Writing subheaders (row 2)...")
    row2 = STATIC_HEADERS[:]
    for _ in all_dates:
        row2.extend(DYNAMIC_HEADERS)
    _write_row_chunked(worksheet, row_number=2, values=row2)

    # ── Step 5: Write case rows ───────────────────────────────────────────────
    if cases:
        logger.info(f"Step 5/5 | Writing {len(cases)} case rows...")
        _write_case_rows(worksheet, cases)
    else:
        logger.info("Step 5/5 | No cases in DB yet. Skipping case rows.")

    # ── Step 6: Apply merged cells ────────────────────────────────────────────
    logger.info("Step 6/6 | Applying merges and color formatting...")
    _apply_enhanced_formatting(worksheet, all_dates, total_rows)

    logger.info("Setup complete.")
    print(f"\n✓ Master Sheet for {year} is ready.")
    print(f"  Columns : {total_cols} ({len(all_dates)} dates × {DYNAMIC_COLS} + {STATIC_COLS} static)")
    print(f"  Cases   : {len(cases)}")


# ══════════════════════════════════════════════════════════════════════════════
# SHEET WRITING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def hex_to_rgb(hex_str):
    hex_str = hex_str.lstrip('#')
    return {
        "red": int(hex_str[0:2], 16)/255.0,
        "green": int(hex_str[2:4], 16)/255.0,
        "blue": int(hex_str[4:6], 16)/255.0
    }

def _write_row_chunked(worksheet: gspread.Worksheet, row_number: int, values: list):
    """Writes a full row in column chunks to stay within API limits."""
    for i in range(0, len(values), COL_WRITE_CHUNK):
        chunk      = values[i:i + COL_WRITE_CHUNK]
        start_col  = _col_letter(i + 1)
        end_col    = _col_letter(i + len(chunk))
        range_name = f"{start_col}{row_number}:{end_col}{row_number}"
        worksheet.update(range_name, [chunk])
        logger.debug(f"Row {row_number} | wrote cols {i+1}–{i+len(chunk)}")
        if i + COL_WRITE_CHUNK < len(values):
            time.sleep(API_DELAY)


def _write_case_rows(worksheet: gspread.Worksheet, cases: list):
    """Writes static columns for all cases starting at row 3."""
    all_cells = []
    for sno, case in enumerate(cases, start=1):
        row_num = 2 + sno
        for col_idx, val in enumerate([
            sno,
            case["case_id"],
            case["case_name"]   or "",
            case["district"]    or "",
            case["prakaran"]    or "",
            case["adhiniyam"]   or "",
            case["old_case_id"] or "",
        ], start=1):
            all_cells.append(gspread.Cell(row=row_num, col=col_idx, value=val))

    for i in range(0, len(all_cells), CELL_WRITE_CHUNK):
        chunk = all_cells[i:i + CELL_WRITE_CHUNK]
        worksheet.update_cells(chunk)
        logger.debug(f"Wrote case cells chunk {i // CELL_WRITE_CHUNK + 1}.")
        if i + CELL_WRITE_CHUNK < len(all_cells):
            time.sleep(API_DELAY)


# ── New Formatting Helper Function ───────────────────────────────────────────

def _apply_enhanced_formatting(worksheet, all_dates, total_rows):
    """Applies merges, background colors, and black separators in one go."""
    all_requests = []
    sheet_id = worksheet.id
    total_cols = STATIC_COLS + (len(all_dates) * DYNAMIC_COLS)

    # 1. STATIC SECTION (Col A - G)
    # Merge A1:G1
    all_requests.append(_merge_request(sheet_id, 0, 0, STATIC_COLS - 1))
    
    # Format Static Headers (Rows 1-2, Cols A-G): Bold, Centered, No Color
    all_requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": STATIC_COLS},
            "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER", "textFormat": {"bold": True}}},
            "fields": "userEnteredFormat(horizontalAlignment,textFormat)"
        }
    })

    # Alternating Grey for Static Data (Row 3 onwards, Col A-G)
    # We use a Conditional Format Rule for "Even/Odd" rows to make it look like a zebra-stripe
    all_requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": total_rows, "startColumnIndex": 0, "endColumnIndex": STATIC_COLS}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=ISODD(ROW())"}]},
                    "format": {"backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95}} # Light Grey
                }
            },
            "index": 0
        }
    })
    all_requests.append({
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": total_rows, "startColumnIndex": 0, "endColumnIndex": STATIC_COLS}],
                "booleanRule": {
                    "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=ISEVEN(ROW())"}]},
                    "format": {"backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85}} # Darker Grey
                }
            },
            "index": 1
        }
    })

    # DYNAMIC DATE SECTIONS
    # 2. Iterate through each date to create Merges, Colors, and Borders
    for i, date_str in enumerate(all_dates):
        col_start = STATIC_COLS + (i * DYNAMIC_COLS)
        col_end = col_start + DYNAMIC_COLS - 1
        
        # Determine the day of the week
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = dt.strftime("%A")
        colors = DAY_COLORS.get(day_name, {"dark": "#ffffff", "light": "#ffffff"})

        # A. Merge Date Header (Row 1)
        all_requests.append(_merge_request(sheet_id, 0, col_start, col_end))

        # B. Apply Dark Color & Centering to Header (Row 1) AND Sub-headers (Row 2)
        all_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 2, # Rows 1 and 2
                    "startColumnIndex": col_start, "endColumnIndex": col_end + 1
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": hex_to_rgb(colors["dark"]),
                        "horizontalAlignment": "CENTER",
                        "textFormat": {"bold": True}
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,horizontalAlignment,textFormat)"
            }
        })

        # C. Apply Light Color to Data Rows (Row 3 onwards)
        all_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 2, "endRowIndex": total_rows,
                    "startColumnIndex": col_start, "endColumnIndex": col_end + 1
                },
                "cell": {"userEnteredFormat": {"backgroundColor": hex_to_rgb(colors["light"])}},
                "fields": "userEnteredFormat.backgroundColor"
            }
        })

        # 3. GLOBAL BORDERS (Apply to Col A to the very last Column)
        # Dashed Horizontal Separators for all rows starting from Row 3
        all_requests.append({
            "updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 2, "endRowIndex": total_rows, "startColumnIndex": 0, "endColumnIndex": total_cols},
                "innerHorizontal": {"style": "DASHED", "color": {"red": 0, "green": 0, "blue": 0}}
            }
        })

        # Solid Borders for Headers (Row 1 and 2)
        all_requests.append({
            "updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": total_cols},
                "innerHorizontal": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "bottom": {"style": "SOLID_MEDIUM", "color": {"red": 0, "green": 0, "blue": 0}}
            }
        })

        # Vertical Block Separators (Black Medium lines between dates and after Static section)
        # Add one for the end of Static columns
        all_requests.append({
            "updateBorders": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": total_rows, "startColumnIndex": STATIC_COLS - 1, "endColumnIndex": STATIC_COLS},
                "right": {"style": "SOLID_MEDIUM", "color": {"red": 0, "green": 0, "blue": 0}}
            }
        })

        # Add vertical separators for each date block
        for i in range(len(all_dates)):
            col_end = STATIC_COLS + (i * DYNAMIC_COLS) + DYNAMIC_COLS - 1
            all_requests.append({
                "updateBorders": {
                    "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": total_rows, "startColumnIndex": col_end, "endColumnIndex": col_end + 1},
                    "right": {"style": "SOLID_MEDIUM", "color": {"red": 0, "green": 0, "blue": 0}}
                }
            })

    # Execute all formatting in batches to prevent timeout
    for i in range(0, len(all_requests), MERGE_BATCH_SIZE):
        batch = all_requests[i:i + MERGE_BATCH_SIZE]
        worksheet.spreadsheet.batch_update({"requests": batch})
        time.sleep(0.5) # Small buffer


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_cases() -> list:
    conn = get_conn()
    try:
        return conn.execute(
            """
            SELECT case_pk, case_id, case_name, district, prakaran, adhiniyam, old_case_id
            FROM Cases ORDER BY case_id ASC
            """
        ).fetchall()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# GENERAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _generate_dates_descending(year: int) -> list:
    """All dates of a year in descending order (Dec 31 → Jan 1). Handles leap years."""
    dates   = []
    current = date(year, 12, 31)
    end     = date(year, 1, 1)
    while current >= end:
        dates.append(current.isoformat())
        current -= timedelta(days=1)
    return dates


def _get_or_create_worksheet(workbook, name: str) -> gspread.Worksheet:
    try:
        return workbook.worksheet(name)
    except gspread.WorksheetNotFound:
        logger.info(f"Worksheet '{name}' not found. Creating it.")
        return workbook.add_worksheet(title=name, rows=100, cols=100)


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


def _col_letter(col_1based: int) -> str:
    result = ""
    while col_1based > 0:
        col_1based, remainder = divmod(col_1based - 1, 26)
        result = chr(65 + remainder) + result
    return result


if __name__ == "__main__":
    main()