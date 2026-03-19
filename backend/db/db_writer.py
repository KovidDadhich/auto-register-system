import sqlite3
from datetime import date as _date, timedelta

from config.settings import DB_PATH
from backend.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTION HELPER
# ══════════════════════════════════════════════════════════════════════════════

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# NEXT DATE WRITER
# ══════════════════════════════════════════════════════════════════════════════

def write_next_date(hearing_id: int, case_pk: int, next_hearing_date_raw: str):
    """
    Called after a successful fetch_next_date scrape.

    Steps:
    1. Update existing Hearings record → set next_hearing_date
    2. Parse next_hearing_date (DD/MM/YYYY from GCMS → YYYY-MM-DD for DB)
    3. Create new Hearings record for the next cycle:
         - prev_hearing_date  = existing record's current_hearing_date
         - current_hearing_date = next_hearing_date (just fetched)
         - bench_fetch_at     = current_hearing_date - 1
         - next_date_fetch_at = current_hearing_date + 2
    """
    conn = get_conn()
    try:
        # ── Step 1: fetch existing record ─────────────────────────────────────
        existing = conn.execute(
            "SELECT current_hearing_date FROM Hearings WHERE hearing_id = ?",
            (hearing_id,),
        ).fetchone()

        if existing is None:
            raise ValueError(f"Hearing record not found for hearing_id={hearing_id}")

        # ── Step 3: parse next_hearing_date → YYYY-MM-DD ─────────────────────  ===============> moved upwards so as to write date as isoformat in next_hearing_date of existing record (earlier it was 03/12/2026, now 2026-12-03 in next_hearing_date also)
        new_current_dt = _parse_gcms_date(next_hearing_date_raw)

        # ── Step 2: update next_hearing_date on existing record ───────────────
        conn.execute(
            "UPDATE Hearings SET next_hearing_date = ? WHERE hearing_id = ?",
            (new_current_dt, hearing_id),
        )
        logger.debug(f"Updated next_hearing_date | hearing_id={hearing_id} | value={new_current_dt}")

        # ── Step 4: compute scheduling dates ──────────────────────────────────
        bench_fetch_at     = (new_current_dt - timedelta(days=1)).isoformat()
        next_date_fetch_at = (new_current_dt + timedelta(days=2)).isoformat()

        # ── Step 5: insert new Hearings record ────────────────────────────────
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
                existing["current_hearing_date"],  # prev = old current
                new_current_dt.isoformat(),
                bench_fetch_at,
                next_date_fetch_at,
            ),
        )
        logger.info(
            f"New Hearings record created | case_pk={case_pk} | "
            f"current_hearing_date={new_current_dt.isoformat()} | "
            f"bench_fetch_at={bench_fetch_at} | "
            f"next_date_fetch_at={next_date_fetch_at}"
        )

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"write_next_date failed | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# BENCH DETAILS WRITER
# ══════════════════════════════════════════════════════════════════════════════

def write_bench_details(hearing_id: int, case_pk: int, parsed: dict):
    """
    Called after a successful fetch_bench scrape.

    Compares fetched_hearing_date with stored current_hearing_date.

    If date unchanged:
        → Update bench_name, bench_number, bench_member, status on existing record.

    If date changed:
        → Update existing record with bench details + mark date_changed +
          clear next_hearing_date and next_date_fetch_at.
        → Insert new Hearings record with corrected current_hearing_date,
          carrying forward prev_hearing_date from the old record
          (old current never happened in court).
    """
    conn = get_conn()
    try:
        # ── Load existing record ──────────────────────────────────────────────
        existing = conn.execute(
            """
            SELECT current_hearing_date, prev_hearing_date
            FROM Hearings
            WHERE hearing_id = ?
            """,
            (hearing_id,),
        ).fetchone()

        if existing is None:
            raise ValueError(f"Hearing record not found for hearing_id={hearing_id}")

        stored_date  = existing["current_hearing_date"]
        fetched_date = parsed["fetched_hearing_date"]

        if stored_date == fetched_date:
            # ── Normal: date unchanged ────────────────────────────────────────
            conn.execute(
                """
                UPDATE Hearings
                SET bench_name   = ?,
                    bench_number = ?,
                    bench_member = ?,
                    status       = ?
                WHERE hearing_id = ?
                """,
                (
                    parsed["bench_name"],
                    parsed["bench_number"],
                    parsed["bench_member"],
                    parsed["status"],
                    hearing_id,
                ),
            )
            logger.debug(
                f"Bench details updated | hearing_id={hearing_id} | date unchanged"
            )

        else:
            # ── Date changed ──────────────────────────────────────────────────
            logger.warning(
                f"Hearing date changed | hearing_id={hearing_id} | "
                f"stored={stored_date} | fetched={fetched_date}"
            )

            # Step 1: mark old record
            conn.execute(
                """
                UPDATE Hearings
                SET bench_name           = ?,
                    bench_number         = ?,
                    bench_member         = ?,
                    status               = ?,
                    next_hearing_date    = NULL,
                    next_date_fetch_at   = NULL,
                    hearing_date_changed = 'date_changed'
                WHERE hearing_id = ?
                """,
                (
                    parsed["bench_name"],
                    parsed["bench_number"],
                    parsed["bench_member"],
                    parsed["status"],
                    hearing_id,
                ),
            )
            logger.debug(f"Old record marked as date_changed | hearing_id={hearing_id}")

            # Step 2: parse new date and compute scheduling
            new_current_dt     = _parse_gcms_date(fetched_date)
            bench_fetch_at     = (new_current_dt - timedelta(days=1)).isoformat()
            next_date_fetch_at = (new_current_dt + timedelta(days=2)).isoformat()

            # Step 3: insert new corrected record
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
                    existing["prev_hearing_date"],  # carry forward — old current never happened
                    new_current_dt.isoformat(),
                    bench_fetch_at,
                    next_date_fetch_at,
                ),
            )
            logger.info(
                f"New corrected Hearings record inserted | case_pk={case_pk} | "
                f"new_current={new_current_dt.isoformat()}"
            )

        conn.commit()

    except Exception as e:
        conn.rollback()
        logger.error(f"write_bench_details failed | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# FETCHQUEUE STATUS WRITERS
# ══════════════════════════════════════════════════════════════════════════════

def mark_task_complete(task_id: int):
    """Marks a FetchQueue task as completed."""
    _update_task_status(task_id, "completed")
    logger.debug(f"Task marked complete | task_id={task_id}")


def mark_task_processing(task_id: int):
    """Marks a FetchQueue task as processing."""
    _update_task_status(task_id, "processing")
    logger.debug(f"Task marked processing | task_id={task_id}")


def mark_task_failed(task_id: int, error_msg: str, new_retry_count: int):
    """
    Marks a FetchQueue task as failed.
    Stores last_error and updated retry_count.
    """
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE FetchQueue
            SET status      = 'failed',
                retry_count = ?,
                last_error  = ?
            WHERE task_id = ?
            """,
            (new_retry_count, error_msg, task_id),
        )
        conn.commit()
        logger.debug(
            f"Task marked failed | task_id={task_id} | "
            f"retry_count={new_retry_count} | error={error_msg}"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"mark_task_failed DB error | task_id={task_id} | error={e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _update_task_status(task_id: int, status: str):
    """Generic helper to update FetchQueue status."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE FetchQueue SET status = ? WHERE task_id = ?",
            (status, task_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"_update_task_status failed | task_id={task_id} | error={e}")
        raise
    finally:
        conn.close()


def _parse_gcms_date(date_str: str) -> _date:
    """
    Parses a date string from GCMS (DD/MM/YYYY) into a Python date object.
    Example: "15/01/2026" → date(2026, 1, 15)
    """
    try:
        day, month, year = date_str.strip().split("/")
        return _date(int(year), int(month), int(day))
    except Exception:
        raise ValueError(f"Could not parse GCMS date: '{date_str}' — expected DD/MM/YYYY")