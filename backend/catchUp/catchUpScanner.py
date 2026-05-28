'''
Purpose: Catch-up scanner for handling missed tasks due to system shutdown.
Runs on every main.py startup before any pipeline.
Iterates until no more missed rows are found.
'''

import sqlite3
from datetime import date, datetime, timedelta

from config.settings import DB_PATH, NEXT_DATE_GRACE_DAYS, BENCH_CUTOFF_HOUR, DATE_CUTOFF_HOUR
from backend.logger import get_logger
from backend.db.db_writer import get_conn

logger = get_logger(__name__)

# ── Shutdown marker values ─────────────────────────────────────────────────────
SHUTDOWN_BENCH_VALUE = "N/A - system shutdown"
SHUTDOWN_NOTE_BENCH  = "Bench details could not be extracted due to system shutdown."
SHUTDOWN_NOTE_NEXT   = "Next hearing date fetched late — possible missed hearing due to system shutdown."

name = 1

# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def run_catchup_scanner():
    """
    Main entry point. Called from main.py before any pipeline runs.
    Iterates until no more missed rows are found.
    Each iteration may create new Hearings rows (via write_next_date),
    which themselves may need catch-up — hence the loop.
    """
    logger.info("Catch-up scanner started.")
    iteration = 0

    while True:
        iteration += 1
        logger.info(f"Catch-up scanner — iteration {iteration}")

        missed_bench     = _find_missed_bench_tasks()
        missed_next_date = _find_missed_next_date_tasks()

        if not missed_bench and not missed_next_date:
            logger.info(f"Catch-up scanner — no missed tasks found. Done after {iteration} iteration(s).")
            break

        logger.info(
            f"Iteration {iteration} | "
            f"missed bench: {len(missed_bench)} | "
            f"missed next_date: {len(missed_next_date)}"
        )

        # Process bench tasks first — they don't create new rows
        for row in missed_bench:
            _handle_missed_bench(row)

        # Process next_date tasks — may create new Hearings rows
        # which will be picked up in the next iteration
        for row in missed_next_date:
            _handle_missed_next_date(row)

    logger.info("Catch-up scanner complete.")


# ══════════════════════════════════════════════════════════════════════════════
# FIND MISSED TASKS
# ══════════════════════════════════════════════════════════════════════════════

def _find_missed_bench_tasks() -> list:
    """
    Finds Hearings rows where:
    - bench_fetch_at is in the past
    - bench_fetched = 0
    - No active (pending/processing) fetch_bench task in FetchQueue
    """
    today = date.today().isoformat()
    conn  = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.hearing_id,
                h.case_pk,
                h.current_hearing_date,
                h.bench_fetch_at,
                c.case_id
            FROM Hearings h
            JOIN Cases c ON h.case_pk = c.case_pk
            WHERE h.bench_fetch_at < ?
              AND h.bench_fetched   = 0
              AND NOT EXISTS (
                SELECT 1 FROM FetchQueue fq
                WHERE fq.hearing_id = h.hearing_id
                  AND fq.task_type  = 'fetch_bench'
                  AND fq.status     IN ('pending', 'processing')
              )
            """,
            (today,),
        ).fetchall()
        logger.debug(f"Found {len(rows)} missed bench tasks.")
        return rows
    finally:
        conn.close()


def _find_missed_next_date_tasks() -> list:
    """
    Finds Hearings rows where:
    - next_date_fetch_at is in the past
    - next_date_fetched = 0
    - No active (pending/processing) fetch_next_date task in FetchQueue
    """
    today = date.today().isoformat()
    conn  = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT
                h.hearing_id,
                h.case_pk,
                h.current_hearing_date,
                h.next_date_fetch_at,
                c.case_id
            FROM Hearings h
            JOIN Cases c ON h.case_pk = c.case_pk
            WHERE h.next_date_fetch_at < ?
              AND h.next_date_fetched   = 0
              AND NOT EXISTS (
                SELECT 1 FROM FetchQueue fq
                WHERE fq.hearing_id = h.hearing_id
                  AND fq.task_type  = 'fetch_next_date'
                  AND fq.status     IN ('pending', 'processing')
              )
            """,
            (today,),
        ).fetchall()
        logger.debug(f"Found {len(rows)} missed next_date tasks.")
        return rows
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# HANDLE MISSED BENCH TASK
# ══════════════════════════════════════════════════════════════════════════════

def _handle_missed_bench(row: sqlite3.Row):
    """
    Case 1.1: today <= current_hearing_date OR same day before cutoff hour
              → create fetch_bench task, normal flow handles it
    Case 1.2: today > current_hearing_date OR same day after cutoff hour
              → mark unrecoverable, set system_note, bench_fetched = 1
    """
    hearing_id           = row["hearing_id"]
    case_pk              = row["case_pk"]
    case_id              = row["case_id"]
    current_hearing_date = row["current_hearing_date"]  # YYYY-MM-DD string

    today     = date.today()
    today_str = today.isoformat()
    now_hour  = datetime.now().hour

    # Parse current_hearing_date
    hearing_dt = date.fromisoformat(current_hearing_date)

    # ── Determine case ────────────────────────────────────────────────────────
    if today < hearing_dt:
        # Before hearing date → still fetchable (Case 1.1)
        _create_catchup_task(hearing_id, case_pk, "fetch_bench", today_str)
        logger.info(
            f"Case 1.1 | fetch_bench | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | queued for today"
        )

    elif today == hearing_dt and now_hour < BENCH_CUTOFF_HOUR:
        # Same day, before cutoff hour → still fetchable (Case 1.1)
        _create_catchup_task(hearing_id, case_pk, "fetch_bench", today_str)
        logger.info(
            f"Case 1.1 | fetch_bench | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | same day before cutoff | queued for today"
        )

    else:
        # Hearing date passed or cutoff reached → unrecoverable (Case 1.2)
        _mark_bench_unrecoverable(hearing_id, case_id, current_hearing_date)


# ══════════════════════════════════════════════════════════════════════════════
# HANDLE MISSED NEXT DATE TASK
# ══════════════════════════════════════════════════════════════════════════════

def _handle_missed_next_date(row: sqlite3.Row):
    """
    Case 2.1: today <= current_hearing_date + GRACE_DAYS
              → create fetch_next_date task, normal flow handles it
    Case 2.2: today > current_hearing_date + GRACE_DAYS
              → create fetch_next_date task with late_fetch flag in system_note
                scraper writes whatever date is on GCMS + system_note
    """
    hearing_id           = row["hearing_id"]
    case_pk              = row["case_pk"]
    case_id              = row["case_id"]
    current_hearing_date = row["current_hearing_date"]  # YYYY-MM-DD string

    today     = date.today()
    today_str = today.isoformat()
    now_hour  = datetime.now().hour

    hearing_dt    = date.fromisoformat(current_hearing_date)
    grace_deadline = hearing_dt + timedelta(days=NEXT_DATE_GRACE_DAYS)


    # ── Determine case ────────────────────────────────────────────────────────
    if today < grace_deadline:
        # Within grace period and Before possible next hearing → normal fetch (Case 2.1)
        _create_catchup_task(hearing_id, case_pk, "fetch_next_date", today_str)
        logger.info(
            f"Case 2.1 | fetch_next_date | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | within grace period | queued for today"
        )

    elif today == grace_deadline and now_hour < DATE_CUTOFF_HOUR:
        # Within grace period and during the possible next hearing → normal fetch (Case 2.1)
        _create_catchup_task(hearing_id, case_pk, "fetch_next_date", today_str)
        logger.info(
            f"Case 2.1 | fetch_next_date | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | within grace period, same day before cutoff | queued for today"
        )

    else:
        # Past grace period → late fetch, possible missed hearing (Case 2.2)
        _create_catchup_task(
            hearing_id, case_pk, "fetch_next_date", today_str,
            late_fetch=True
        )
        logger.warning(
            f"Case 2.2 | fetch_next_date | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | past grace period | "
            f"late fetch queued — possible missed hearing"
        )


# ══════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _create_catchup_task(
    hearing_id     : int,
    case_pk        : int,
    task_type      : str,
    scheduled_date : str,
    late_fetch     : bool = False,
):
    """
    Inserts a new queued task in FetchQueue for catch-up processing.
    For Case 2.2, stores 'late_fetch' in last_error so task_processor
    knows to add system_note after writing.
    """
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO FetchQueue
                (case_pk, hearing_id, task_type, scheduled_date, status, retry_count, last_error)
            VALUES
                (?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                case_pk,
                hearing_id,
                task_type,
                scheduled_date,
                "late_fetch" if late_fetch else None, # only for Case 2.2
            ),
        )
        conn.commit()
        logger.debug(
            f"Catch-up task created | hearing_id={hearing_id} | "
            f"task_type={task_type} | late_fetch={late_fetch}"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to create catch-up task | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()


def _mark_bench_unrecoverable(
    hearing_id           : int,
    case_id              : str,
    current_hearing_date : str,
):
    """
    Case 1.2 — bench details are unrecoverable.
    Fills bench fields with shutdown marker, sets system_note, bench_fetched = 1.
    """
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE Hearings
            SET bench_name    = ?,
                bench_number  = ?,
                bench_member  = ?,
                status        = ?,
                system_note   = ?,
                bench_fetched = 1
            WHERE hearing_id  = ?
            """,
            (
                SHUTDOWN_BENCH_VALUE,
                SHUTDOWN_BENCH_VALUE,
                SHUTDOWN_BENCH_VALUE,
                SHUTDOWN_BENCH_VALUE,
                SHUTDOWN_NOTE_BENCH,
                hearing_id,
            ),
        )
        conn.commit()
        logger.warning(
            f"Case 1.2 | bench unrecoverable | case_id={case_id} | "
            f"hearing_date={current_hearing_date} | marked with system_note"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"_mark_bench_unrecoverable failed | hearing_id={hearing_id} | error={e}")
        raise
    finally:
        conn.close()