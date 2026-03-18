import json
import os
from backend.logger import get_logger

logger = get_logger(__name__)

# ── Path to mapping dictionary ─────────────────────────────────────────────────
from config.settings import MAPPING_FILE


# ── Load mapping once at import time ──────────────────────────────────────────
def _load_mapping() -> dict:
    """
    Loads bench_mapping.json from config/.
    Called once when the module is first imported.
    """
    try:
        with open(MAPPING_FILE, encoding="utf-8") as f:
            mapping = json.load(f)
        logger.info(f"Bench mapping loaded. {len(mapping)} entries found.")
        return mapping
    except FileNotFoundError:
        logger.error(f"bench_mapping.json not found at: {MAPPING_FILE}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"bench_mapping.json is invalid JSON: {e}")
        return {}


BENCH_MAPPING: dict = _load_mapping()


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def map_bench_name(hindi_name: str | None) -> str | None:
    """
    Maps a Hindi bench name to its English register format.

    Example:
        Input  : "एस. बी-6"
        Output : "S.B.-6"  (as per your mapping)

    If not found in dictionary → returns raw Hindi text as-is.
    If input is None           → returns None.
    """
    if hindi_name is None:
        return None

    hindi_name = hindi_name.strip()

    mapped = BENCH_MAPPING.get(hindi_name)

    if mapped:
        logger.debug(f"Mapped bench_name: '{hindi_name}' → '{mapped}'")
        return mapped
    else:
        logger.warning(f"bench_name not found in mapping: '{hindi_name}' — storing as-is.")
        return hindi_name


def map_parsed_data(parsed: dict) -> dict:
    """
    Receives the parsed dict from task_processor and returns
    a new dict with bench_name mapped to English.

    All other fields (bench_number, bench_member, status,
    fetched_hearing_date, next_hearing_date) are passed through unchanged.

    Example input:
        {
            "bench_name"          : "एस. बी-6",
            "bench_number"        : "R-28",
            "bench_member"        : "श्री हेमन्‍त कुमार गेरा, अध्‍यक्ष",
            "status"              : "विचाराधीन",
            "fetched_hearing_date": "15/01/2026",
        }

    Example output:
        {
            "bench_name"          : "S.B.-6",
            "bench_number"        : "R-28",
            "bench_member"        : "श्री हेमन्‍त कुमार गेरा, अध्‍यक्ष",
            "status"              : "विचाराधीन",
            "fetched_hearing_date": "15/01/2026",
        }
    """
    mapped = dict(parsed)  # copy so original is not mutated
    mapped["bench_name"] = map_bench_name(parsed.get("bench_name"))
    return mapped


def reload_mapping():
    """
    Reloads the mapping dictionary from disk.
    Call this if you update bench_mapping.json without restarting the server.
    """
    global BENCH_MAPPING
    BENCH_MAPPING = _load_mapping()
    logger.info("Bench mapping reloaded.")