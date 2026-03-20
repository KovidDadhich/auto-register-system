'''
Purpose: Daily scheduler for the register system pipeline.
Runs two jobs daily at configurable times:
  1. Main pipeline  (Queue Builder → Task Processor → Sheets Sync)
  2. Retry pipeline (Retry Handler → Task Processor)

  


Pipeline functions and blocking scheduler for the Register System.
 
- run_main_pipeline()  → called by main.py --main or by scheduler
- run_retry_pipeline() → called by main.py --retry or by scheduler
- start_scheduler()    → called by main.py --scheduler (server mode, runs 24/7)

'''

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

from config.settings import MAIN_RUN_TIME, RETRY_RUN_TIME
from backend.logger import get_logger
from backend.queue.queue_builder import build_queue_for_today
from backend.queue.retry_handler import run_retry_handler
from backend.scraper.task_processor import (
    run_fetch_next_hearing_date,
    run_fetch_bench_details,
)
from backend.catchUp.catchUpScanner import run_catchup_scanner

from backend.sheets_sync.sheets_sync import run_sheets_sync

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE STEPS
# ══════════════════════════════════════════════════════════════════════════════

def run_main_pipeline():
    """
    Main daily pipeline.
 
    Steps:
    0. Catch-up Scanner  → handles missed tasks from any system shutdown
    1. Queue Builder     → creates today's fetch tasks
    2. Task Processor    → scrapes GCMS, writes to DB via db_writer
    3. Sheets Sync       → pushes DB data to Google Sheets (placeholder)
    """
    logger.info("=" * 60)
    logger.info("MAIN PIPELINE STARTED")
    logger.info("=" * 60)

    # ── Step 0: Catch-up Scanner ──────────────────────────────────────────────
    logger.info("Step 0/3 | Catch-up Scanner starting...")
    try:
        run_catchup_scanner()
        logger.info("Step 0/3 | Catch-up Scanner complete.")
    except Exception as e:
        logger.error(f"Step 0/3 | Catch-up Scanner FAILED: {e}")
        logger.error("Main pipeline stopped.")
        return



    # ── Step 1: Queue Builder ─────────────────────────────────────────────────
    logger.info("Step 1/3 | Queue Builder starting...")
    try:
        build_queue_for_today()
        logger.info("Step 1/3 | Queue Builder complete.")
    except Exception as e:
        logger.error(f"Step 1/3 | Queue Builder FAILED: {e}")
        logger.error("Main pipeline stopped.")
        return

    # ── Step 2: Task Processor ────────────────────────────────────────────────
    logger.info("Step 2/3 | Task Processor starting...")
    try:
        run_fetch_next_hearing_date()
        run_fetch_bench_details()
        logger.info("Step 2/3 | Task Processor complete.")
    except Exception as e:
        logger.error(f"Step 2/3 | Task Processor FAILED: {e}")
        logger.error("Main pipeline stopped.")
        return

    # ── Step 3: Sheets Sync ────────────────────────────────────
    logger.info("Step 3/3 | Sheets Sync starting...")
    try:
        run_sheets_sync()
        logger.info("Step 3/3 | Sheets Sync complete.")
    except Exception as e:
        logger.error(f"Step 3/3 | Sheets Sync FAILED: {e}")
        logger.error("Main pipeline stopped.")
        return

    logger.info("=" * 60)
    logger.info("MAIN PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("=" * 60)


def run_retry_pipeline():
    """
    Retry pipeline. Runs after main pipeline (next day at RETRY_RUN_TIME).

    Steps:
    1. Retry Handler  → re-queues failed tasks from previous main run
    2. Task Processor → scrapes GCMS for re-queued tasks, writes to DB
    """
    logger.info("=" * 60)
    logger.info("RETRY PIPELINE STARTED")
    logger.info("=" * 60)

    # ── Step 1: Retry Handler ─────────────────────────────────────────────────
    logger.info("Step 1/2 | Retry Handler starting...")
    try:
        run_retry_handler()
        logger.info("Step 1/2 | Retry Handler complete.")
    except Exception as e:
        logger.error(f"Step 1/2 | Retry Handler FAILED: {e}")
        logger.error("Retry pipeline stopped.")
        return

    # ── Step 2: Task Processor ────────────────────────────────────────────────
    logger.info("Step 2/2 | Task Processor (retry) starting...")
    try:
        run_fetch_next_hearing_date()
        run_fetch_bench_details()
        logger.info("Step 2/2 | Task Processor (retry) complete.")
    except Exception as e:
        logger.error(f"Step 2/2 | Task Processor (retry) FAILED: {e}")
        logger.error("Retry pipeline stopped.")
        return

    logger.info("=" * 60)
    logger.info("RETRY PIPELINE COMPLETED SUCCESSFULLY")
    logger.info("=" * 60)


# ══════════════════════════════════════════════════════════════════════════════
# PLACEHOLDER
# ══════════════════════════════════════════════════════════════════════════════

def _sheets_sync_placeholder():
    """
    Placeholder for Google Sheets sync.
    Replace this with the actual call once sheets_syncer.py is built:
        run_sheets_sync()
    """
    logger.info("Sheets Sync | Placeholder — not yet implemented. Skipping.")


# ══════════════════════════════════════════════════════════════════════════════
# BLOCKING SCHEDULER (server mode)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_time(time_str: str) -> tuple:
    """
    Parses a time string "HH:MM" into (hour, minute) integers.
    Example: "08:00" → (8, 0)
    """
    try:
        hour, minute = time_str.strip().split(":")
        return int(hour), int(minute)
    except Exception:
        raise ValueError(f"Invalid time format: '{time_str}' — expected HH:MM")


def start_scheduler():
    """
    Starts the blocking APScheduler for server mode.
    Runs indefinitely — call via python main.py --scheduler.
 
    Catch-up scanner is embedded inside run_main_pipeline() so it
    runs automatically on each scheduled main pipeline execution.
    """
    main_hour,  main_minute  = _parse_time(MAIN_RUN_TIME)
    retry_hour, retry_minute = _parse_time(RETRY_RUN_TIME)

    scheduler = BlockingScheduler()

    # ── Main pipeline job ─────────────────────────────────────────────────────
    scheduler.add_job(
        func        = run_main_pipeline,
        trigger     = "cron",
        hour        = main_hour,
        minute      = main_minute,
        id          = "main_pipeline",
        name        = "Main Pipeline",
        max_instances = 1,          # prevent overlap if previous run is still going
        misfire_grace_time = 600,   # if PC was off, allow up to 10 min late start
    )

    # ── Retry pipeline job ────────────────────────────────────────────────────
    scheduler.add_job(
        func        = run_retry_pipeline,
        trigger     = "cron",
        hour        = retry_hour,
        minute      = retry_minute,
        id          = "retry_pipeline",
        name        = "Retry Pipeline",
        max_instances = 1,
        misfire_grace_time = 600,
    )

    # ── Event listener for job errors ─────────────────────────────────────────
    def _on_job_event(event):
        if event.exception:
            logger.error(
                f"Scheduler job crashed | job={event.job_id} | "
                f"error={event.exception}"
            )
        else:
            logger.info(f"Scheduler job finished | job={event.job_id}")

    scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    logger.info(
        f"Blocking scheduler started | "
        f"main_pipeline={MAIN_RUN_TIME} | retry_pipeline={RETRY_RUN_TIME}"
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"Scheduler crashed: {e}")
        raise