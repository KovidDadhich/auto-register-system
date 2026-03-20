from dotenv import load_dotenv
import os
from pathlib import Path


# -----------------------------
# Load Environment Variables
# -----------------------------
load_dotenv("config/.env")


# -----------------------------
# Project Paths
# -----------------------------
DB_PATH = os.getenv("DB_PATH", "")
LOG_FILE_PATH = os.getenv("LOG_FILE", "")


# -----------------------------
# GCMS Website
# -----------------------------
URL = os.getenv("URL", "")


# -----------------------------
# Scraper Settings
# -----------------------------
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 5))
SCRAPER_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", 10))
# MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", 3))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 5))
BATCH_DELAY_MIN = float(os.getenv("BATCH_DELAY_MIN", 3.0))
BATCH_DELAY_MAX = float(os.getenv("BATCH_DELAY_MAX", 8.0))


# ------------------------------> THIS ROLE NOW DONE BY WINDOWS TASK SCHEDULER
# Scheduler Settings
# -----------------------------
MAIN_RUN_TIME  = os.getenv("MAIN_RUN_TIME",  "08:00")
RETRY_RUN_TIME = os.getenv("RETRY_RUN_TIME", "11:00")


# -----------------------------
# Mapper
# -----------------------------
BASE_DIR = os.getenv("BASE_DIR", "auto-register-system")
MAPPING_FILE = os.path.join("config", "bench_mapping.json")


# ------------------------------
# CatchUp Scanner
# ------------------------------
NEXT_DATE_GRACE_DAYS = int(os.getenv("NEXT_DATE_GRACE_DAYS", 4))
BENCH_CUTOFF_HOUR    = int(os.getenv("BENCH_CUTOFF_HOUR", 19))
DATE_CUTOFF_HOUR    = int(os.getenv("DATE_CUTOFF_HOUR", 19))


# ------------------------------
# Sheets
# ------------------------------
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "config/service_account.json")










# from dotenv import load_dotenv
# import os
# from pathlib import Path

# # -----------------------------
# # Load Environment Variables
# # -----------------------------
# load_dotenv("config/.env")




# # -----------------------------
# # Queue Settings
# # -----------------------------
# QUEUE_PENDING = "pending"
# QUEUE_COMPLETED = "completed"
# QUEUE_FAILED = "failed"



# # -----------------------------
# # Google Sheets Settings
# # -----------------------------
# GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Master Register")

# # -----------------------------
# # Logging Settings
# # -----------------------------
# LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")