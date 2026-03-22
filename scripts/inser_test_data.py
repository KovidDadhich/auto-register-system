'''
Purpose: Interactive script to insert test data into the database.
Inserts one Case + one Hearings record at a time.
Hearings record has both bench_fetch_at and next_date_fetch_at set to today
so the full pipeline can be tested immediately.

Usage:
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
    while True:
        value = input(f"  {label}: ").strip()
        if value:
            return value
        if not required:
            return ""
        print(f"  x {label} is required. Please enter a value.")


def prompt_date(label: str) -> str:
    while True:
        value = input(f"  {label} (DD/MM/YYYY): ").strip()
        try:
            day, month, year = value.split("/")
            db_date = date(int(year), int(month), int(day))
            return db_date.isoformat()
        except Exception:
            print("  x Invalid date format. Please use DD/MM/YYYY.")


def case_exists(conn, case_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM Cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    return row is not None


def show_summary(case: dict, hearing: dict):
    print("\n  -- Summary ---------------------------------------------------")
    print(f"  Case ID              : {case['case_id']}")
    print(f"  Case Name            : {case['case_name']}")
    print(f"  District             : {case['district']}")
    print(f"  Tehsil               : {case['tehsil'] or '-'}")
    print(f"  Old Case ID          : {case['old_case_id'] or '-'}")
    print(f"  Connected Prakaran   : {case['connected_prakaran'] or '-'}")
    print(f"  Prakaran             : {case['prakaran']}")
    print(f"  Adhiniyam            : {case['adhiniyam']}")
    print(f"  To Be Continued?     : {case['to_be_continued'] or '-'}")
    print(f"  Client In Contact?   : {case['client_in_contact'] or '-'}")
    print(f"  -------------------------------------------------------------")
    print(f"  Current Hearing      : {hearing['current_hearing_date']}")
    print(f"  Prev Hearing         : {hearing['prev_hearing_date'] or '-'}")
    print(f"  bench_fetch_at       : {hearing['bench_fetch_at']}  <- set to today")
    print(f"  next_date_fetch_at   : {hearing['next_date_fetch_at']} <- set to today")
    print(f"  -------------------------------------------------------------")


def insert_case_and_hearing(conn, case: dict, hearing: dict):
    try:
        conn.execute(
            """
            INSERT INTO Cases (
                case_id, case_name, district, tehsil, old_case_id,
                connected_prakaran, prakaran, adhiniyam,
                to_be_continued, client_in_contact
            )
            VALUES (
                :case_id, :case_name, :district, :tehsil, :old_case_id,
                :connected_prakaran, :prakaran, :adhiniyam,
                :to_be_continued, :client_in_contact
            )
            """,
            case,
        )

        case_pk = conn.execute(
            "SELECT case_pk FROM Cases WHERE case_id = ?", (case["case_id"],)
        ).fetchone()["case_pk"]

        conn.execute(
            """
            INSERT INTO Hearings (
                case_pk,
                prev_hearing_date,
                current_hearing_date,
                bench_fetch_at,
                next_date_fetch_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                case_pk,
                hearing["prev_hearing_date"] or None,
                hearing["current_hearing_date"],
                hearing["bench_fetch_at"],
                hearing["next_date_fetch_at"],
            ),
        )

        conn.commit()
        print(f"\n  Case '{case['case_id']}' inserted successfully (case_pk={case_pk}).")

    except Exception as e:
        conn.rollback()
        print(f"\n  x Insert failed: {e}")
        raise


def main():
    today = date.today().isoformat()
    today_display = date.today().strftime("%d-%m-%Y")

    print("=" * 60)
    print("  Register System - Test Data Entry")
    print(f"  Today's date: {today_display}")
    print("  Both bench_fetch_at and next_date_fetch_at will be")
    print("  set to today to enable full pipeline testing.")
    print("=" * 60)

    conn = get_conn()

    while True:
        print("\n-- New Case Entry ---------------------------------------------")

        case_id = prompt("Case ID (as on GCMS)")

        if case_exists(conn, case_id):
            print(f"  x Case ID '{case_id}' already exists in database. Skipping.")
        else:
            case = {
                "case_id"            : case_id,
                "case_name"          : prompt("Case Name"),
                "district"           : prompt("District"),
                "tehsil"             : prompt("Tehsil (press Enter to skip)", required=False),
                "old_case_id"        : prompt("Old Case ID (press Enter to skip)", required=False),
                "connected_prakaran" : prompt("Connected Prakaran (press Enter to skip)", required=False),
                "prakaran"           : prompt("Prakaran"),
                "adhiniyam"          : prompt("Adhiniyam"),
                "to_be_continued"    : prompt("To Be Continued? (press Enter to skip)", required=False),
                "client_in_contact"  : prompt("Client In Contact? (press Enter to skip)", required=False),
            }

            print("\n  Hearing details:")
            current_hearing_date = prompt_date("Current Hearing Date")
            prev_hearing_date = ""
            add_prev = input("  Add previous hearing date? (y/n): ").strip().lower()
            if add_prev == "y":
                prev_hearing_date = prompt_date("Previous Hearing Date")

            hearing = {
                "prev_hearing_date"   : prev_hearing_date or None,
                "current_hearing_date": current_hearing_date,
                "bench_fetch_at"      : today,
                "next_date_fetch_at"  : today,
            }

            show_summary(case, hearing)
            confirm = input("\n  Confirm insert? (y/n): ").strip().lower()

            if confirm == "y":
                insert_case_and_hearing(conn, case, hearing)
            else:
                print("  Skipped.")

        again = input("\n  Add another case? (y/n): ").strip().lower()
        if again != "y":
            break

    conn.close()
    print("\n  Done. Exiting.")
    print("=" * 60)


if __name__ == "__main__":
    main()