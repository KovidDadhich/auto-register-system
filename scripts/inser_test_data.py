'''
Purpose: Interactive script to insert test cases into the database.
Inserts one Cases record + one Hearings record per case.
Hearings record has both bench_fetch_at and next_date_fetch_at set to today
so the full pipeline can be tested immediately.

Run with:
    python scripts/insert_test_data.py
'''

import sqlite3
import os
import sys
from datetime import date, timedelta

# ── Path setup ─────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from config.settings import DB_PATH


# ── DB Connection ──────────────────────────────────────────────────────────────
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── Helpers ────────────────────────────────────────────────────────────────────
def prompt(label: str, required: bool = True) -> str:
    """Prompts user for input. If required, keeps asking until non-empty."""
    while True:
        value = input(f"  {label}: ").strip()
        if value:
            return value
        if not required:
            return ""
        print(f"  ✗ {label} is required. Please enter a value.")


def prompt_date(label: str) -> str:
    """Prompts user for a date in DD/MM/YYYY format. Converts to YYYY-MM-DD for DB."""
    while True:
        value = input(f"  {label} (DD/MM/YYYY): ").strip()
        if not value:
            print(f"  ✗ {label} is required.")
            continue
        try:
            day, month, year = value.split("/")
            parsed = date(int(year), int(month), int(day))
            return parsed.isoformat()  # store as YYYY-MM-DD in DB
        except Exception:
            print("  ✗ Invalid date format. Please use DD/MM/YYYY.")


def case_exists(conn: sqlite3.Connection, case_id: str) -> bool:
    """Returns True if a case with this case_id already exists."""
    row = conn.execute(
        "SELECT 1 FROM Cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    return row is not None


def insert_case(conn: sqlite3.Connection, data: dict) -> int:
    """Inserts a Cases record. Returns the new case_pk."""
    cursor = conn.execute(
        """
        INSERT INTO Cases (case_id, case_name, district, prakaran, adhiniyam, old_case_id)
        VALUES (:case_id, :case_name, :district, :prakaran, :adhiniyam, :old_case_id)
        """,
        data,
    )
    return cursor.lastrowid


def insert_hearing(conn: sqlite3.Connection, case_pk: int, current_hearing_date: str):
    """
    Inserts a Hearings record with:
    - current_hearing_date = as entered
    - bench_fetch_at       = today (so fetch_bench runs today)
    - next_date_fetch_at   = today (so fetch_next_date runs today)
    """
    today = date.today().isoformat()

    conn.execute(
        """
        INSERT INTO Hearings (
            case_pk,
            current_hearing_date,
            bench_fetch_at,
            next_date_fetch_at
        ) VALUES (?, ?, ?, ?)
        """,
        (case_pk, current_hearing_date, today, today),
    )


def show_summary(conn: sqlite3.Connection, case_pk: int, case_id: str):
    """Prints inserted records for confirmation."""
    case = conn.execute(
        "SELECT * FROM Cases WHERE case_pk = ?", (case_pk,)
    ).fetchone()
    hearing = conn.execute(
        "SELECT * FROM Hearings WHERE case_pk = ? ORDER BY hearing_id DESC LIMIT 1",
        (case_pk,)
    ).fetchone()

    print("\n  ── Inserted Successfully ─────────────────────────────")
    print(f"  Cases record:")
    print(f"    case_pk             : {case['case_pk']}")
    print(f"    case_id             : {case['case_id']}")
    print(f"    case_name           : {case['case_name']}")
    print(f"    district            : {case['district']}")
    print(f"    prakaran            : {case['prakaran']}")
    print(f"    adhiniyam           : {case['adhiniyam']}")
    print(f"    old_case_id         : {case['old_case_id'] or '—'}")
    print(f"  Hearings record:")
    print(f"    hearing_id          : {hearing['hearing_id']}")
    print(f"    current_hearing_date: {hearing['current_hearing_date']}")
    print(f"    bench_fetch_at      : {hearing['bench_fetch_at']}  ← set to today")
    print(f"    next_date_fetch_at  : {hearing['next_date_fetch_at']}  ← set to today")
    print("  ─────────────────────────────────────────────────────\n")


# ── Main Loop ──────────────────────────────────────────────────────────────────
def main():
    print("\n" + "=" * 60)
    print("  Register System — Test Data Entry")
    print(f"  Database: {DB_PATH}")
    print(f"  Today   : {date.today().strftime('%d-%m-%Y')}")
    print("=" * 60)

    conn = get_conn()
    total_inserted = 0

    while True:
        print(f"\n── Case #{total_inserted + 1} ─────────────────────────────────────")
        print("  Enter case details (press Enter to skip optional fields):\n")

        # ── Case fields ───────────────────────────────────────────────────────
        case_id = prompt("case_id (required)")

        # Check for duplicates
        if case_exists(conn, case_id):
            print(f"\n  ⚠ case_id '{case_id}' already exists in DB. Skipping.\n")
        else:
            case_name   = prompt("case_name (required)")
            district    = prompt("district (required)")
            prakaran    = prompt("prakaran (required)")
            adhiniyam   = prompt("adhiniyam (required)")
            old_case_id = prompt("old_case_id (optional, press Enter to skip)", required=False)

            # ── Hearing fields ─────────────────────────────────────────────────
            print()
            current_hearing_date = prompt_date("current_hearing_date")

            # ── Insert ─────────────────────────────────────────────────────────
            try:
                case_pk = insert_case(conn, {
                    "case_id"    : case_id,
                    "case_name"  : case_name,
                    "district"   : district,
                    "prakaran"   : prakaran,
                    "adhiniyam"  : adhiniyam,
                    "old_case_id": old_case_id or None,
                })
                insert_hearing(conn, case_pk, current_hearing_date)
                conn.commit()
                total_inserted += 1
                show_summary(conn, case_pk, case_id)

            except Exception as e:
                conn.rollback()
                print(f"\n  ✗ Insert failed: {e}\n")

        # ── Continue prompt ────────────────────────────────────────────────────
        again = input("  Add another case? (y/n): ").strip().lower()
        if again != "y":
            break

    conn.close()
    print(f"\n{'=' * 60}")
    print(f"  Done. {total_inserted} case(s) inserted.")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()