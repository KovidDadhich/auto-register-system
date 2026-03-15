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
DATABASE_PATH = os.getenv("DB_PATH", "")
LOG_FILE_PATH = os.getenv("LOG_FILE", "")


# -----------------------------
# GCMS Website
# -----------------------------
URL = os.getenv("URL", "")



MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
SCRAPER_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", 10))







# from dotenv import load_dotenv
# import os
# from pathlib import Path

# # -----------------------------
# # Load Environment Variables
# # -----------------------------
# load_dotenv("config/.env")


# # -----------------------------
# # Scraper Settings
# # -----------------------------
# MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))
# SCRAPER_TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", 10))
# MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", 3))
# BATCH_SIZE = int(os.getenv("BATCH_SIZE", 5))

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