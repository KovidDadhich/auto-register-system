'''
Main entry point for the Register System.
 
Usage:
    python main.py --scheduler   → start blocking APScheduler (for server, runs 24/7)
    python main.py --main        → run main pipeline and exit (for Windows PC)
    python main.py --retry       → run retry pipeline and exit (for Windows PC)
    python main.py --now         → run both pipelines and exit (for manual testing)
 
Windows Task Scheduler setup:
    Task 1: python main.py --main   at 21:00 daily
    Task 2: python main.py --retry  at 13:30 daily
 
Server setup:
    python main.py --scheduler   (runs forever, handles scheduling internally)
'''

import os
import sys
import argparse
from datetime import datetime

from backend.logger import get_logger
from backend.scheduler.scheduler import (
    run_main_pipeline,
    run_retry_pipeline,
    start_scheduler,
)
from backend.catchUp.catchUpScanner import run_catchup_scanner

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# STARTUP CHECKS
# ══════════════════════════════════════════════════════════════════════════════

def verify_startup():
    """
    Runs basic checks before starting the scheduler.
    If any check fails, the system will not start.
    """
    errors = []
 
    from config.settings import DB_PATH, MAPPING_FILE, URL, MAIN_RUN_TIME, RETRY_RUN_TIME
 
    if not os.path.exists(DB_PATH):
        errors.append(f"Database not found at: {DB_PATH} — run scripts/create_database.py first.")
    if not os.path.exists(MAPPING_FILE):
        errors.append(f"Bench mapping file not found at: {MAPPING_FILE}")
    if not URL:
        errors.append("URL is not set in config/settings.py or .env")
    if not MAIN_RUN_TIME:
        errors.append("MAIN_RUN_TIME is not set in .env")
    if not RETRY_RUN_TIME:
        errors.append("RETRY_RUN_TIME is not set in .env")
 
    if errors:
        logger.error("Startup checks failed. System will not start.")
        for err in errors:
            logger.error(f"  ✗ {err}")
        sys.exit(1)
 
    logger.info("All startup checks passed.")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
 
    # ── Parse CLI arguments ───────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Register System")
    group  = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--scheduler",
        action="store_true",
        help="Start blocking APScheduler — for server use, runs 24/7"
    )
    group.add_argument(
        "--main",
        action="store_true",
        help="Run main pipeline immediately and exit — for Windows Task Scheduler"
    )
    group.add_argument(
        "--retry",
        action="store_true",
        help="Run retry pipeline immediately and exit — for Windows Task Scheduler"
    )
    group.add_argument(
        "--now",
        action="store_true",
        help="Run both pipelines immediately and exit — for manual testing"
    )
    args = parser.parse_args()
 
    # ── Require at least one flag ─────────────────────────────────────────────
    if not any([args.scheduler, args.main, args.retry, args.now]):
        parser.print_help()
        print("\nError: please provide one of --scheduler, --main, --retry, --now")
        sys.exit(1)
 
    # ── Startup banner ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("REGISTER SYSTEM STARTING")
    logger.info(f"Time: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
    logger.info("=" * 60)
 
    # ── Verify before running anything ───────────────────────────────────────
    verify_startup()
 
    # ── Run based on flag ─────────────────────────────────────────────────────
    if args.scheduler:
        # Server mode — blocking scheduler runs 24/7
        # Catch-up scanner is called inside run_main_pipeline() on each scheduled run
        logger.info("Mode: --scheduler → starting blocking APScheduler.")
        start_scheduler()
 
    elif args.main:
        # Windows PC mode — run main pipeline and exit
        logger.info("Mode: --main → running catch-up scanner + main pipeline.")
        run_catchup_scanner()
        run_main_pipeline()
 
    elif args.retry:
        # Windows PC mode — run retry pipeline and exit
        logger.info("Mode: --retry → running catch-up scanner + retry pipeline.")
        run_catchup_scanner()
        run_retry_pipeline()
 
    elif args.now:
        # Manual testing — run both and exit
        logger.info("Mode: --now → running catch-up scanner + both pipelines.")
        run_catchup_scanner()
        run_main_pipeline()
        run_retry_pipeline()

    # ── Done (not reached in --scheduler mode) ────────────────────────────────
    logger.info("=" * 60)
    logger.info("REGISTER SYSTEM STARTING UP")
    logger.info("=" * 60)
    sys.exit(0)



















#-----------------------------------------------------------------------------------



# from config.settings import URL
# from backend.scraper.gcms_scraper import GCMSScraper
# from scripts.create_database import create_DB
# from scripts.insert_test_case import insertTestCase

# def run_scraper():
#     scraper = GCMSScraper()
#     scraper.initialize_session()

#     data = scraper.fetch_case("2009/7852")

#     print(data)


# def main():
#     create_DB()

#     # insert-test-case
#     insertTestCase()

#     # print(f"Starting Register System for: {URL}")
#     # # Call your scraper function
#     # run_scraper()

# if __name__ == "__main__":
#     main()


