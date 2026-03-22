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
STATIC_COLS       = 11
DYNAMIC_COLS      = 8
STATIC_HEADERS    = ["S. No.", "Case ID", "Case Name", "District", "Tehsil", "Old Case ID", "Connected Prakaran", "Prakaran", "Adhiniyam", "To Be Continued?", "Client In Contact?"]
DYNAMIC_HEADERS   = ["Prev Hearing Date", "Current Hearing Date", "Bench Name", "Bench Number", "Bench Member", "Status", "Comments", "Next Hearing Date"]
WORKSHEET_NAME    = "Master Sheet"
API_DELAY         = 1.5    # seconds between API calls
MERGE_BATCH_SIZE  = 200     # merge requests per batch_update call
COL_WRITE_CHUNK   = 200    # columns per row write chunk
CELL_WRITE_CHUNK  = 500    # cells per update_cells call

DAY_COLORS = {
    "Monday":    {"dark": "#ffb565", "light": "#ffd39d"}, # Orange
    "Tuesday":   {"dark": "#72c753", "light": "#d1f6a7"}, # Green
    "Wednesday": {"dark": "#5cb3ff", "light": "#b3daff"}, # Cornflower
    "Thursday":  {"dark": "#a37cff", "light": "#debaff"}, # Purple
    "Friday":    {"dark": "#ffd045", "light": "#fff4ad"}, # Yellow
    "Saturday":  {"dark": "#40dcf8", "light": "#c1f8ff"}, # Cyan
    "Sunday":    {"dark": "#da5891", "light": "#ffc9db"}, # Magenta
}

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/setup_sheet.py <year> [--sheets-only]")
        print("Example: python scripts/setup_sheet.py 2026")
        print("Example: python scripts/setup_sheet.py 2026 --sheets-only")
        sys.exit(1)

    try:
        year = int(sys.argv[1])
    except ValueError:
        print(f"Invalid year: {sys.argv[1]}")
        sys.exit(1)

    sheets_only = "--sheets-only" in sys.argv

    sheet_id = os.getenv(f"SHEET_ID_{year}")
    if not sheet_id:
        print(f"Error: SHEET_ID_{year} not set in .env")
        sys.exit(1)

    # ── Authenticate ──────────────────────────────────────────────────────────
    creds     = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc        = gspread.authorize(creds)
    workbook  = gc.open_by_key(sheet_id)


    # ══════════════════════════════════════════════════════════════════════════
    # --sheets-only: skip Master Sheet, only create two fetch sheets
    # ══════════════════════════════════════════════════════════════════════════
    if sheets_only:
        print(f"\nWorkbook: {workbook.title}")
        print("Mode: --sheets-only")
        print("This will create/overwrite 'Daily_Causelist_Fetch' and 'Case_History_Fetch'.")
        print("Master Sheet will NOT be touched.")
        confirm = input("Type 'yes' to confirm: ").strip().lower()
        if confirm != "yes":
            print("Aborted.")
            sys.exit(0)
 
        logger.info("Creating Daily_Causelist_Fetch sheet...")
        dcf_sheet = _get_or_create_worksheet(workbook, "Daily_Causelist_Fetch")
        _setup_daily_causelist_sheet(dcf_sheet, workbook)
        time.sleep(API_DELAY)
 
        logger.info("Creating Case_History_Fetch sheet...")
        chf_sheet = _get_or_create_worksheet(workbook, "Case_History_Fetch")
        _setup_case_history_sheet(chf_sheet, workbook)
        time.sleep(API_DELAY)
 
        print(f"\n✓ Daily_Causelist_Fetch sheet created.")
        print(f"✓ Case_History_Fetch sheet created.")
        _print_apps_script_instructions()
        sys.exit(0)


    # ══════════════════════════════════════════════════════════════════════════
    # Full setup: rebuild Master Sheet + create both fetch sheets
    # ══════════════════════════════════════════════════════════════════════════
    worksheet = _get_or_create_worksheet(workbook, WORKSHEET_NAME)

    # ── Confirm ───────────────────────────────────────────────────────────────
    print(f"\nThis will CLEAR and REBUILD the '{WORKSHEET_NAME}' sheet for year {year}.")
    print(f"It will also create 'Daily_Causelist_Fetch' and 'Case_History_Fetch' sheets.")
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

    logger.info("Master Sheet setup complete.")
 

    # ── Create Daily_Causelist_Fetch sheet ────────────────────────────────────
    logger.info("Creating Daily_Causelist_Fetch sheet...")
    dcf_sheet = _get_or_create_worksheet(workbook, "Daily_Causelist_Fetch")
    _setup_daily_causelist_sheet(dcf_sheet, workbook)
    time.sleep(API_DELAY)
 
    # ── Create Case_History_Fetch sheet ───────────────────────────────────────
    logger.info("Creating Case_History_Fetch sheet...")
    chf_sheet = _get_or_create_worksheet(workbook, "Case_History_Fetch")
    _setup_case_history_sheet(chf_sheet, workbook)
    time.sleep(API_DELAY)
 
    logger.info("All setup complete.")
    print(f"\n✓ Master Sheet for {year} is ready.")
    print(f"  Columns : {total_cols} ({len(all_dates)} dates × {DYNAMIC_COLS} + {STATIC_COLS} static)")
    print(f"  Cases   : {len(cases)}")
    print(f"\n✓ Daily_Causelist_Fetch sheet created.")
    print(f"\n✓ Case_History_Fetch sheet created.")
    _print_apps_script_instructions()
 
 
def _print_apps_script_instructions():
    print(f"\n{'='*60}")
    print("IMPORTANT: Apps Script setup required for Case_History_Fetch.")
    print("Follow these steps:")
    print("  1. Open the Google Sheet in browser (use Incognito if needed)")
    print("  2. Go to Extensions → Apps Script")
    print("  3. Delete any existing code")
    print("  4. Paste the code from: scripts/case_history_apps_script.js")
    print("  5. Save (Ctrl+S) and close Apps Script editor")
    print("  6. Type a Case ID in cell B1 of Case_History_Fetch to test")
    print(f"{'='*60}")



# ══════════════════════════════════════════════════════════════════════════════
# DAILY_CAUSELIST_FETCH SHEET SETUP
# ══════════════════════════════════════════════════════════════════════════════
 
def _setup_daily_causelist_sheet(worksheet: gspread.Worksheet, workbook: gspread.Spreadsheet):
    """
    Sets up Daily_Causelist_Fetch sheet.
 
    Layout:
      A1: "Select Date:"   B1: [user types DD/MM/YYYY here]
      A2: blank
      A3: headers (Case ID, Case Name, District, Prakaran, Adhiniyam,
                   Old Case ID, Prev Hearing Date, Current Hearing Date,
                   Bench Name, Bench Number, Bench Member, Status,
                   Comments, Next Hearing Date)
      A4 onwards: FILTER formula pulls matching rows from Master Sheet
 
    Formula logic:
      - MATCH finds the column in Master Sheet row 1 that starts with typed date
      - That column = start of Prev Hearing Date for that date group
      - Current Hearing Date is offset +1 from that
      - FILTER returns all rows where Current Hearing Date = typed date
      - Then selects static cols + 8 dynamic cols for that date
    """
    worksheet.clear()
    time.sleep(API_DELAY)
 
    # ── Labels and input cell ─────────────────────────────────────────────────
    worksheet.update("A1:B1", [["Select Date (DD/MM/YYYY):", ""]])
    time.sleep(API_DELAY)
 
    # ── Headers row 3 ────────────────────────────────────────────────────────
    headers = [
        "Case ID", "Case Name", "District", "Tehsil", "Old Case ID",
        "Connected Prakaran", "Prakaran", "Adhiniyam",
        "To Be Continued?", "Client In Contact?",
        "Prev Hearing Date", "Current Hearing Date", "Bench Name", "Bench Number",
        "Bench Member", "Status", "Comments", "Next Hearing Date"
    ]
    worksheet.update("A3:R3", [headers])
    time.sleep(API_DELAY)
 
    # ── Formula in A4 ─────────────────────────────────────────────────────────
    # MATCH finds col number where row1 header starts with typed date (wildcard)
    # OFFSET then grabs the 8 dynamic columns starting from that col
    # FILTER returns rows where Current Hearing Date (col+1) matches input
    
    # Static cols in Master Sheet: A=S.No., B=Case ID ... K=Client In Contact?
    # Dynamic cols start at col L (STATIC_COLS + 1 = 12th col, 1-based)
    # So staticData = B3:K (cols 2-11, Case ID to Client In Contact?)
    # Dynamic data: 8 cols starting at dateCol
    # Current Hearing Date = dateCol + 1 (2nd dynamic col)
    formula = (
        '=IFERROR('
        'LET('
        'dateInput, B1, '
        'masterSheet, INDIRECT("\'Master Sheet\'!A:ZZZ"), '
        # Find column index of the matching date group in Master row 1
        'dateCol, MATCH(dateInput&"*", INDIRECT("\'Master Sheet\'!1:1"), 0), '
        # Static cols: B,C,D,E,F,G (Case ID to Old Case ID) from Master
        'staticData, INDIRECT("\'Master Sheet\'!B3:K"), '
        # Dynamic data: 8 cols starting at dateCol from Master rows 3 onwards
        'dynData, INDEX(INDIRECT("\'Master Sheet\'!A3:ZZZ"), 0, SEQUENCE(1,8,dateCol)), '
        # Current Hearing Date is offset +1 from dateCol (2nd dynamic col)
        'currentDateCol, INDEX(INDIRECT("\'Master Sheet\'!A3:ZZZ"), 0, dateCol+1), '
        # FILTER rows where Current Hearing Date = input date
        'FILTER(HSTACK(staticData, dynData), currentDateCol=dateInput)'
        '),'
        '"No data found for this date."'
        ')'
    )
    worksheet.update("A4", [[formula]])
    time.sleep(API_DELAY)
 
    logger.info("Daily_Causelist_Fetch sheet set up.")
 
 
# ══════════════════════════════════════════════════════════════════════════════
# CASE_HISTORY_FETCH SHEET SETUP
# ══════════════════════════════════════════════════════════════════════════════
 
def _setup_case_history_sheet(worksheet: gspread.Worksheet, workbook: gspread.Spreadsheet):
    """
    Sets up Case_History_Fetch sheet structure.
    Actual data population is handled by Apps Script (case_history_apps_script.js).
 
    Layout:
      A1: "Case ID:"   B1: [user types case ID here]
      A3: Static column headers
      A4: Static column values (filled by Apps Script)
      A5: Dynamic column headers
      A6 onwards: One row per hearing date, newest first (filled by Apps Script)
    """
    worksheet.clear()
    time.sleep(API_DELAY)
 
    # ── Label and input cell ──────────────────────────────────────────────────
    worksheet.update("A1:B1", [["Case ID:", ""]])
    time.sleep(API_DELAY)
 
    # ── Static headers row 3 ─────────────────────────────────────────────────
    static_headers = ["Case ID", "Case Name", "District", "Tehsil", "Old Case ID",
        "Connected Prakaran", "Prakaran", "Adhiniyam",
        "To Be Continued?", "Client In Contact?"]
    worksheet.update("A3", [static_headers])
    time.sleep(API_DELAY)
 
    # ── Dynamic headers row 5 ────────────────────────────────────────────────
    dynamic_headers = [
        "Hearing Date", "Prev Hearing Date", "Current Hearing Date",
        "Bench Name", "Bench Number", "Bench Member",
        "Status", "Comments", "Next Hearing Date"
    ]
    worksheet.update("A5", [dynamic_headers])
    time.sleep(API_DELAY)
 
    # ── Placeholder text ──────────────────────────────────────────────────────
    worksheet.update("A4", [["← Type a Case ID in B1 to load data"]])
    worksheet.update("A6", [["← Hearing history will appear here after typing Case ID"]])
    time.sleep(API_DELAY)
 
    logger.info("Case_History_Fetch sheet structure set up.")





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
            case["case_name"]          or "",
            case["district"]           or "",
            case["tehsil"]             or "",
            case["old_case_id"]        or "",
            case["connected_prakaran"] or "",
            case["prakaran"]           or "",
            case["adhiniyam"]          or "",
            case["to_be_continued"]    or "",
            case["client_in_contact"]  or "",
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
            SELECT case_pk, case_id, case_name, district, tehsil,
                   old_case_id, connected_prakaran, prakaran,
                   adhiniyam, to_be_continued, client_in_contact
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