'''
Purpose: Same-day retry handler.
Runs at a fixed time after the main scraping cycle (configured in scheduler).
Finds failed tasks from today and re-queues them if retries remain.
'''

import sqlite3
from datetime import date

from config.settings import DB_PATH, MAX_RETRIES
from backend.logger import get_logger

logger = get_logger(__name__)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def run_retry_handler():
    """
    Main entry point. Called by scheduler a few hours after main scraping cycle.

    Logic:
    1. Find all failed tasks from today where retry_count < MAX_RETRIES
    2. Skip any hearing+task_type combo that already has a completed task today
       (means a previous same-day retry already succeeded)
    3. Create new queued task rows for the rest
    """
    today = date.today().isoformat()
    today_display = date.today().strftime("%d-%m-%Y")
    logger.info(f"Retry handler started for date: {today_display}")

    conn = get_db_connection()
    try:
        failed_tasks = _load_failed_tasks(conn, today)

        if not failed_tasks:
            logger.info("No failed tasks to retry today.")
            return

        logger.info(f"Found {len(failed_tasks)} failed task(s) to evaluate.")

        retried  = 0
        skipped  = 0

        tasks_to_insert = []

        for task in failed_tasks:
            case_pk    = task["case_pk"]
            hearing_id = task["hearing_id"]
            task_type  = task["task_type"]
            retry_count = task["retry_count"]

            # Skip if a completed task already exists for this hearing+type today
            if _has_completed_task(conn, hearing_id, task_type, today):
                logger.debug(
                    f"Skipping retry | hearing_id={hearing_id} | "
                    f"task_type={task_type} | reason=already completed today"
                )
                skipped += 1
                continue

            # Skip if retries exhausted
            if retry_count >= MAX_RETRIES:
                logger.warning(
                    f"Skipping retry | hearing_id={hearing_id} | "
                    f"task_type={task_type} | reason=max retries reached ({retry_count}/{MAX_RETRIES})"
                )
                skipped += 1
                continue

            tasks_to_insert.append({
                "case_pk"       : case_pk,
                "hearing_id"    : hearing_id,
                "task_type"     : task_type,
                "scheduled_date": today,
                "status"        : "queued",
                "retry_count"   : 0,
                "last_error"    : None,
            })
            retried += 1

        if tasks_to_insert:
            conn.executemany(
                """
                INSERT INTO FetchQueue
                    (case_pk, hearing_id, task_type, scheduled_date, status, retry_count, last_error)
                VALUES
                    (:case_pk, :hearing_id, :task_type, :scheduled_date, :status, :retry_count, :last_error)
                """,
                tasks_to_insert,
            )
            conn.commit()

        logger.info(
            f"Retry handler complete | "
            f"re-queued: {retried} | skipped: {skipped}"
        )

    except Exception as e:
        conn.rollback()
        logger.error(f"Retry handler failed, rolled back: {e}")
        raise
    finally:
        conn.close()


# ── DB Helpers ────────────────────────────────────────────────────────────────

def _load_failed_tasks(conn: sqlite3.Connection, today: str) -> list:
    """
    Returns all failed tasks from today where retry_count < MAX_RETRIES.
    Gets the latest failed task per hearing_id + task_type combination.
    """
    cursor = conn.execute(
        """
        SELECT
            fq.task_id,
            fq.case_pk,
            fq.hearing_id,
            fq.task_type,
            fq.retry_count,
            fq.last_error
        FROM FetchQueue fq
        WHERE fq.status         = 'failed'
          AND fq.scheduled_date = ?
          AND fq.retry_count    < ?
        ORDER BY fq.task_id DESC
        """,
        (today, MAX_RETRIES),
    )
    rows = cursor.fetchall()
    logger.debug(f"Found {len(rows)} failed tasks eligible for retry.")
    return rows


def _has_completed_task(
    conn       : sqlite3.Connection,
    hearing_id : int,
    task_type  : str,
    today      : str,
) -> bool:
    """
    Returns True if a completed task already exists for this
    hearing_id + task_type + today.
    Means a previous retry already succeeded — no need to retry again.
    """
    cursor = conn.execute(
        """
        SELECT 1 FROM FetchQueue
        WHERE hearing_id     = ?
          AND task_type      = ?
          AND scheduled_date = ?
          AND status         = 'completed'
        LIMIT 1
        """,
        (hearing_id, task_type, today),
    )
    return cursor.fetchone() is not None