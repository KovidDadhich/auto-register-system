import logging
import os
from logging.handlers import RotatingFileHandler

# ── Constants ──────────────────────────────────────────────────────────────────
LOG_FILE    = "logs/register_system.log"
MAX_BYTES   = 5 * 1024 * 1024   # 5 MB
BACKUP_COUNT = 5                 # keep last 5 rotated files

# ── Ensure logs/ directory exists ─────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

# ── Formatter ─────────────────────────────────────────────────────────────────
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

# ── File Handler (rotating by size) ───────────────────────────────────────────
file_handler = RotatingFileHandler(
    filename     = LOG_FILE,
    maxBytes     = MAX_BYTES,
    backupCount  = BACKUP_COUNT,
    encoding     = "utf-8",
)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.DEBUG)   # capture everything going to file

# ── Console Handler ───────────────────────────────────────────────────────────
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)  # only INFO+ shown in terminal

# ── Root Logger Setup ─────────────────────────────────────────────────────────
logging.basicConfig(
    level    = logging.DEBUG,   # root level: accept DEBUG and above
    handlers = [file_handler, console_handler],
)




# ── Helper: get a named logger ─────────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger.
    Usage:
        from backend.logger import get_logger
        logger = get_logger(__name__)
    """
    return logging.getLogger(name)


# ── Helper: log a scraping event in a structured way ──────────────────────────
def log_scrape_event(
    logger        : logging.Logger,
    case_id       : str,
    task_type     : str,
    result        : str,                  # "success" | "failed" | "retry"
    response_time : float  = None,        # seconds
    error_message : str    = None,
) -> None:
    """
    Logs a single scraping event with all standard fields.

    Example log line:
        2025-01-15 09:32:11 | INFO     | scraper | case_id=RJ1234 | task_type=fetch_bench | result=success | response_time=1.23s
    """
    parts = [
        f"case_id={case_id}",
        f"task_type={task_type}",
        f"result={result}",
    ]

    if response_time is not None:
        parts.append(f"response_time={response_time:.2f}s")

    if error_message:
        parts.append(f"error={error_message}")

    message = " | ".join(parts)

    if result == "success":
        logger.info(message)
    elif result == "retry":
        logger.warning(message)
    elif result == "failed":
        logger.error(message)
    else:
        logger.debug(message)