'''
Purpose: Apply borders to existing Master Sheet

Usage:
    python scripts/apply_borders.py 2026
    python scripts/apply_borders.py 2027


    
-----------IMPORTTANT------------
THIS PROGRAM NEEDS TO BE TERMINATED MANUALLY AFTER YOU SEE THAT ALL BORDERS ARE COMPLETE
TERMINATE BY PRESSING --------->          CTRL + C       in terminal

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

# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/apply_borders.py <year>")
        print("Example: python scripts/apply_borders.py 2026")
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


    # ══════════════════════════════════════════════════════════════════════════
    # Only apply borders in Master Sheet
    # ══════════════════════════════════════════════════════════════════════════
    worksheet = _get_or_create_worksheet(workbook, WORKSHEET_NAME)

    # ── Confirm ───────────────────────────────────────────────────────────────
    print(f"\nThis will add Borders to '{WORKSHEET_NAME}' sheet for year {year}.")
    print(f"Workbook: {workbook.title}")
    confirm = input("Type 'yes' to confirm: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    # ── Generate all dates Dec 31 → Jan 1 ────────────────────────────────────
    all_dates  = _generate_dates_descending(year)
    total_rows = 2000   # 2 header rows + cases + buffer

    logger.info("\Applying border formatting...")
    _apply_enhanced_formatting(worksheet, all_dates, total_rows)

# ── New Formatting Helper Function ───────────────────────────────────────────

def _apply_enhanced_formatting(worksheet, all_dates, total_rows):
    """Applies black separators"""
    all_requests = []
    sheet_id = worksheet.id
    total_cols = STATIC_COLS + (len(all_dates) * DYNAMIC_COLS)

    # 2. Iterate through each date to create Merges, Colors, and Borders
    for i, date_str in enumerate(all_dates):
        col_start = STATIC_COLS + (i * DYNAMIC_COLS)
        col_end = col_start + DYNAMIC_COLS - 1

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

if __name__ == "__main__":
    main()