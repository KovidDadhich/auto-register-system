'''
Purpose: Creates scraping tasks for today.

'''

import sqlite3
from datetime import date
from config.settings import DB_PATH,MAX_RETRIES
from backend.logger import get_logger, log_scrape_event

logger = get_logger(__name__)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows accessing columns by name
    return conn


def build_queue_for_today():
    """
    Main entry point.
    Checks DB for cases due today and creates tasks in FetchQueue.
    Called once daily by the scheduler.
    """
    today = date.today().isoformat()   # "YYYY-MM-DD"
    today_dd_mm_yyyy = date.today().strftime("%d-%m-%Y")   # shows as DD-MM-YYYY
    logger.info(f"Queue builder started for date: {today_dd_mm_yyyy}")

    conn = get_db_connection()
    try:
        next_date_count = _create_fetch_next_date_tasks(conn, today)
        bench_count     = _create_fetch_bench_tasks(conn, today)
        conn.commit()
        logger.info(
            f"Queue build complete | "
            f"fetch_next_date tasks: {next_date_count} | "
            f"fetch_bench tasks: {bench_count}"
        )
    except Exception as e:
        conn.rollback()
        logger.error(f"Queue build failed, rolled back: {e}")
        raise
    finally:
        conn.close()


# ── Step 1: fetch_next_date tasks ─────────────────────────────────────────────

def _create_fetch_next_date_tasks(conn: sqlite3.Connection, today: str) -> int:
    """
    Finds all Hearings rows where next_date_fetch_at = today.
    Creates one 'fetch_next_date' task per row in FetchQueue.
    Returns count of tasks created.
    """
    rows = _query_hearings_by_date(conn, column="next_date_fetch_at", date=today)

    if not rows:
        logger.info("No fetch_next_date tasks to create today.")
        return 0

    created = _insert_tasks_with_check(conn, rows, "fetch_next_date", today)
    logger.info(f"Created {created} fetch_next_date tasks (skipped {len(rows) - created} already pending/processing).")
    return created


# ── Step 2: fetch_bench tasks ─────────────────────────────────────────────────

def _create_fetch_bench_tasks(conn: sqlite3.Connection, today: str) -> int:
    """
    Finds all Hearings rows where bench_fetch_at = today.
    Creates one 'fetch_bench' task per row in FetchQueue.
    Returns count of tasks created.
    """
    rows = _query_hearings_by_date(conn, column="bench_fetch_at", date=today)

    if not rows:
        logger.info("No fetch_bench tasks to create today.")
        return 0

    created = _insert_tasks_with_check(conn, rows, "fetch_bench", today)
    logger.info(f"Created {created} fetch_bench tasks (skipped {len(rows) - created} already pending/processing).")
    return created


# ── DB Helpers ────────────────────────────────────────────────────────────────

def _query_hearings_by_date(
    conn   : sqlite3.Connection,
    column : str,
    date   : str,
) -> list:
    """
    Returns all Hearings rows where the given date column = date.
    Joins with Cases to make case_pk available.
    """
    query = f"""
        SELECT
            h.hearing_id,
            h.case_pk,
            c.case_id
        FROM Hearings h
        JOIN Cases c ON h.case_pk = c.case_pk
        WHERE h.{column} = ?
    """
    cursor = conn.execute(query, (date,))
    rows   = cursor.fetchall()
    logger.debug(f"Query on {column} = {date} returned {len(rows)} rows.")
    return rows


def _is_task_blocked(
    conn        : sqlite3.Connection,
    case_pk     : int,
    hearing_id  : int,
    task_type   : str,
    scheduled_date: str,
) -> bool:
    """
    Returns True (blocked — do NOT create task) if:
      - a completed task already exists for this hearing+type+date
        (same-day retry already succeeded), OR
      - status is 'pending' or 'processing' (already in progress), OR
      - status is 'failed' AND retry_count >= MAX_RETRIES (exhausted)
 
    Returns False (allowed — create new task) if:
      - no task exists yet, OR
      - last task was 'failed' but retries still remaining
    """
    # Check if any completed task exists for this hearing+type+date
    completed = conn.execute(
        """
        SELECT 1 FROM FetchQueue
        WHERE hearing_id     = ?
          AND task_type      = ?
          AND scheduled_date = ?
          AND status         = 'completed'
        LIMIT 1
        """,
        (hearing_id, task_type, scheduled_date),
    ).fetchone()
 
    if completed:
        logger.debug(
            f"Skipping | hearing_id={hearing_id} | task_type={task_type} | "
            f"reason=already completed (same-day retry succeeded)"
        )
        return True                                   # already succeeded → skip

    # Check latest task status
    cursor = conn.execute(
        """
        SELECT status, retry_count FROM FetchQueue
        WHERE case_pk      = ?
          AND hearing_id   = ?
          AND task_type    = ?
          AND scheduled_date = ?
        ORDER BY task_id DESC
        LIMIT 1
        """,
        (case_pk, hearing_id, task_type, scheduled_date),
    )
    row = cursor.fetchone()

    if row is None:
        return False  # no task exists → create one

    status = row["status"]
    retry_count = row["retry_count"]

    if status in ("pending", "processing"):
        return True   # already in progress → skip
    
    if status == "failed" and retry_count >= MAX_RETRIES:
        logger.warning(
            f"Max retries reached | case_pk={case_pk} | "
            f"task_type={task_type} | retry_count={retry_count}/{MAX_RETRIES}"
        )
        return True     # retries exhausted → skip

    return False      # failed with retries left, or completed → allow / create a new one


def _insert_tasks_with_check(
    conn          : sqlite3.Connection,
    rows          : list,
    task_type     : str,
    scheduled_date: str,
) -> int:
    """
    For each hearing row, checks if a task should be created.
    Inserts only the tasks that pass the check.
    Returns count of tasks actually inserted.
    """
    tasks_to_insert = []

    for row in rows:
        blocked = _is_task_blocked(
            conn, row["case_pk"], row["hearing_id"], task_type, scheduled_date
        )
        if blocked:
            logger.debug(
                f"Skipped task | case_pk={row['case_pk']} | "
                f"task_type={task_type} | status=pending/processing/max_retries"
            )
        else:
            tasks_to_insert.append({
                "case_pk"       : row["case_pk"],
                "hearing_id"    : row["hearing_id"],
                "task_type"     : task_type,
                "scheduled_date": scheduled_date,
                "status"        : "pending",
                "retry_count"   : 0,
                "last_error"    : None,
            })

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
        logger.debug(f"Inserted {len(tasks_to_insert)} {task_type} tasks into FetchQueue.")

    return len(tasks_to_insert)
