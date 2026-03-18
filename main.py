'''
Main entry point for the Register System.
Run this file to start the system:
    python main.py
'''

import os
import sys

from backend.logger import get_logger
from backend.scheduler.scheduler import start_scheduler

logger = get_logger(__name__)


def verify_startup():
    """
    Runs basic checks before starting the scheduler.
    If any check fails, the system will not start.
    """
    errors = []

    # ── Check 1: Database file exists ─────────────────────────────────────────
    from config.settings import DB_PATH
    if not os.path.exists(DB_PATH):
        errors.append(f"Database not found at: {DB_PATH} — run scripts/create_database.py first.")

    # ── Check 2: Bench mapping file exists ────────────────────────────────────
    from config.settings import MAPPING_FILE
    if not os.path.exists(MAPPING_FILE):
        errors.append(f"Bench mapping file not found at: {MAPPING_FILE}")

    # ── Check 3: Required settings are present ────────────────────────────────
    from config.settings import URL, MAIN_RUN_TIME, RETRY_RUN_TIME
    if not URL:
        errors.append("URL is not set in config/settings.py or .env")
    if not MAIN_RUN_TIME:
        errors.append("MAIN_RUN_TIME is not set in .env")
    if not RETRY_RUN_TIME:
        errors.append("RETRY_RUN_TIME is not set in .env")

    # ── Report ─────────────────────────────────────────────────────────────────
    if errors:
        logger.error("Startup checks failed. System will not start.")
        for err in errors:
            logger.error(f"  ✗ {err}")
        sys.exit(1)

    logger.info("All startup checks passed.")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("REGISTER SYSTEM STARTING UP")
    logger.info("=" * 60)

    verify_startup()
    start_scheduler()



















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


