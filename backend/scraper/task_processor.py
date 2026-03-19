import asyncio
import aiohttp
import random
import re
import sqlite3
from datetime import date
from bs4 import BeautifulSoup

from config.settings import (
    URL,
    MAX_RETRIES,
    SCRAPER_TIMEOUT,
    BATCH_SIZE,
    BATCH_DELAY_MIN,
    BATCH_DELAY_MAX,
)
from backend.logger import get_logger, log_scrape_event
from backend.mapper.hindi_mapper import map_parsed_data
from backend.db.db_writer import (
    write_next_date,
    write_bench_details,
    mark_task_complete,
    mark_task_processing,
    mark_task_failed,
    get_conn,
    _parse_gcms_date,
)

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
HEADERS = {
    "X-MicrosoftAjax"   : "Delta=true",
    "X-Requested-With"  : "XMLHttpRequest",
    "Referer"           : URL,
    "Origin"            : "https://gcms.rajasthan.gov.in",
    "User-Agent"        : "Mozilla/5.0",
}

SEMAPHORE_LIMIT = 3   # max concurrent requests at once


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def run_fetch_next_hearing_date():
    """
    Entry point called by scheduler.
    Picks all queued 'fetch_next_date' tasks and processes them.
    """
    logger.info("Starting fetch_next_hearing_date processor.")
    asyncio.run(_process_tasks(task_type="fetch_next_date"))
    logger.info("fetch_next_hearing_date processor complete.")


def run_fetch_bench_details():
    """
    Entry point called by scheduler.
    Picks all queued 'fetch_bench' tasks and processes them.
    """
    logger.info("Starting fetch_bench_details processor.")
    asyncio.run(_process_tasks(task_type="fetch_bench"))
    logger.info("fetch_bench_details processor complete.")


# ══════════════════════════════════════════════════════════════════════════════
# CORE ASYNC PIPELINE
# ══════════════════════════════════════════════════════════════════════════════

async def _process_tasks(task_type: str):
    """
    Loads all queued tasks of the given type from FetchQueue.
    Processes them in batches with a random delay between batches.
    Each task gets its own aiohttp session (own viewstate + CSRF).
    """
    tasks = _load_queued_tasks(task_type)

    if not tasks:
        logger.info(f"No queued tasks found for task_type='{task_type}'.")
        return

    logger.info(f"Loaded {len(tasks)} tasks for task_type='{task_type}'.")

    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

    # Split into batches
    batches = [
        tasks[i : i + BATCH_SIZE]
        for i in range(0, len(tasks), BATCH_SIZE)
    ]

    for batch_num, batch in enumerate(batches, start=1):
        logger.info(f"Processing batch {batch_num}/{len(batches)} ({len(batch)} tasks).")

        # Run all tasks in the batch concurrently (limited by semaphore)
        await asyncio.gather(*[
            _handle_task(task, task_type, semaphore)
            for task in batch
        ])

        # Random delay between batches (not after the last one)
        if batch_num < len(batches):
            delay = random.uniform(BATCH_DELAY_MIN, BATCH_DELAY_MAX)
            logger.info(f"Batch {batch_num} done. Waiting {delay:.1f}s before next batch.")
            await asyncio.sleep(delay)


async def _handle_task(task: sqlite3.Row, task_type: str, semaphore: asyncio.Semaphore):
    """
    Handles a single task:
    1. Acquires semaphore slot
    2. Initializes its own aiohttp session
    3. Scrapes GCMS
    4. Parses + maps result
    5. Writes to DB
    6. Updates FetchQueue status
    """
    task_id    = task["task_id"]
    case_pk    = task["case_pk"]
    hearing_id = task["hearing_id"]
    case_id    = task["case_id"]
    retry_count = task["retry_count"]

    async with semaphore:
        mark_task_processing(task_id)

        try:
            async with aiohttp.ClientSession() as session:
                viewstate, generator, csrf = await _initialize_session(session)
                raw_html = await _scrape_gcms(
                    session, case_id, viewstate, generator, csrf
                )

            parsed = _parse_response(raw_html, task_type, case_id)

            if parsed is None:
                raise ValueError("Parsing returned None — required fields missing in HTML.")

            # Map Hindi → English (bench_name only, others pass through)
            if task_type == "fetch_bench":
                parsed = map_parsed_data(parsed)

            # Write to DB via db_writer
            if task_type == "fetch_next_date":
                write_next_date(hearing_id, case_pk, parsed["next_hearing_date"])
            elif task_type == "fetch_bench":
                write_bench_details(hearing_id, case_pk, parsed)

            mark_task_complete(task_id)

            log_scrape_event(
                logger,
                case_id   = case_id,
                task_type = task_type,
                result    = "success",
            )

        except Exception as e:
            error_msg       = str(e)
            new_retry_count = retry_count + 1
            mark_task_failed(task_id, error_msg, new_retry_count)

            result_label = "retry" if new_retry_count < MAX_RETRIES else "failed"
            log_scrape_event(
                logger,
                case_id       = case_id,
                task_type     = task_type,
                result        = result_label,
                error_message = error_msg,
            )


# ══════════════════════════════════════════════════════════════════════════════
# SESSION INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════

async def _initialize_session(session: aiohttp.ClientSession) -> tuple:
    """
    GETs the GCMS page to extract viewstate, generator, and CSRF token.
    Returns (viewstate, generator, csrf).
    """
    timeout = aiohttp.ClientTimeout(total=SCRAPER_TIMEOUT)
    async with session.get(URL, headers=HEADERS, timeout=timeout) as res:
        html = await res.text()

    viewstate = _extract_hidden(html, "__VIEWSTATE")
    generator = _extract_hidden(html, "__VIEWSTATEGENERATOR")
    csrf      = _extract_hidden(html, "ctl00$MainContent$hiddenCsrf")

    logger.debug(f"Session initialized. viewstate length: {len(viewstate)}")
    return viewstate, generator, csrf


# ══════════════════════════════════════════════════════════════════════════════
# GCMS REQUEST
# ══════════════════════════════════════════════════════════════════════════════

async def _scrape_gcms(
    session   : aiohttp.ClientSession,
    case_id   : str,
    viewstate : str,
    generator : str,
    csrf      : str,
) -> str:
    """
    POSTs the search payload to GCMS and returns the extracted UpdatePanel HTML.
    """
    current_date = date.today().strftime("%d/%m/%Y")

    payload = {
        "ctl00$MainContent$sm1"               : "ctl00$MainContent$UpdatePanelSearch|ctl00$MainContent$btnSearchCase",
        "__EVENTTARGET"                        : "",
        "__EVENTARGUMENT"                      : "",
        "__LASTFOCUS"                          : "",
        "__VIEWSTATE"                          : viewstate,
        "__VIEWSTATEGENERATOR"                 : generator,
        "ctl00$MainContent$hiddenCsrf"         : csrf,
        "ctl00$MainContent$rbCaseSearch"       : "0",
        "ctl00$MainContent$txtCompID"          : case_id,
        "ctl00$MainContent$txtPetitionerName"  : "",
        "ctl00$MainContent$txtPartivadiName"   : "",
        "ctl00$MainContent$ddlDistrict"        : "",
        "ctl00$MainContent$ddlAct"             : "",
        "ctl00$MainContent$ddlCaseTypeGroup"   : "",
        "ctl00$MainContent$ddlCasePurpose"     : "",
        "ctl00$MainContent$txthrDate"          : "",
        "ctl00$MainContent$txtInstfrmDt"       : "",
        "ctl00$MainContent$txtInsttoDt"        : "",
        "ctl00$MainContent$txtCaseAdvocate"    : "",
        "ctl00$MainContent$hfCaseAdvRegNo"     : "",
        "ctl00$MainContent$rblDecisionStatus"  : "FALSE",
        "ctl00$MainContent$rbCauseList"        : "0",
        "ctl00$MainContent$txtBorCSDate"       : current_date,
        "ctl00$MainContent$txtBorHDate"        : "",
        "ctl00$MainContent$rblBorCauseListCourt": "BOARD OF REVENUE,AJMER",
        "ctl00$MainContent$rblAdvCauseListCourt": "ALL",
        "ctl00$MainContent$rblAdvCauseListType" : "ALL",
        "ctl00$MainContent$txtCauseAdvocate"   : "",
        "ctl00$MainContent$hfCauseAdvocate"    : "",
        "ctl00$MainContent$rblOldCauseListType": "ALL",
        "ctl00$MainContent$rbDecision"         : "0",
        "ctl00$MainContent$txtCaseID"          : "",
        "ctl00$MainContent$ddlDecDistrict"     : "",
        "ctl00$MainContent$ddlDecCaseType"     : "",
        "ctl00$MainContent$ddlDecAct"          : "",
        "ctl00$MainContent$txtDecFromDate"     : "",
        "ctl00$MainContent$txtDecToDate"       : "",
        "ctl00$MainContent$txtDecMember"       : "",
        "ctl00$MainContent$rblWorthWise"       : "F",
        "ctl00$MainContent$txtAppCaseID"       : "",
        "ctl00$MainContent$hfActiveCase"       : "0",
        "ctl00$MainContent$selected_tab"       : "#tabCaseSearch",
        "ctl00$hdnIP"                          : "",
        "__ASYNCPOST"                          : "true",
        "ctl00$MainContent$btnSearchCase"      : "देखें",
    }

    timeout = aiohttp.ClientTimeout(total=SCRAPER_TIMEOUT)
    async with session.post(URL, data=payload, headers=HEADERS, timeout=timeout) as res:
        raw = await res.text()

    html = _extract_updatepanel(raw)
    if html is None:
        raise ValueError("UpdatePanel not found in GCMS response.")

    return html


# ══════════════════════════════════════════════════════════════════════════════
# HTML PARSING
# ══════════════════════════════════════════════════════════════════════════════

def _parse_response(html: str, task_type: str, case_id: str) -> dict | None:
    """
    Parses the UpdatePanel HTML and extracts fields based on task_type.

    fetch_next_date  → { next_hearing_date }
    fetch_bench      → { bench_name, bench_number, bench_member, status,
                         fetched_hearing_date }
                       (fetched_hearing_date always included for date-change check)

    Returns None if required fields are missing.
    """
    soup = BeautifulSoup(html, "html.parser")

    if task_type == "fetch_next_date":
        next_date = _get_value(soup, "सुनवाई/निर्णय दिनांक")
        if not next_date:
            logger.warning(f"case_id={case_id} | next_hearing_date not found in HTML.")
            return None
        return {"next_hearing_date": _parse_gcms_date(next_date).isoformat}

    elif task_type == "fetch_bench":
        bench_raw            = _get_value(soup, "बेंच")
        bench_member         = _get_value(soup, "सदस्य विवरण")
        status               = _get_value(soup, "प्रकरण की स्तिथि")
        fetched_hearing_date_raw = _get_value(soup, "सुनवाई/निर्णय दिनांक")  # for date-change check
        fetched_hearing_date = _parse_gcms_date(fetched_hearing_date_raw).isoformat() # for date-change check

        if not bench_raw:
            logger.warning(f"case_id={case_id} | bench not found in HTML.")
            return None

        bench_name, bench_number = _split_bench(bench_raw)

        return {
            "bench_name"          : bench_name,     # Hindi text → mapper will translate later
            "bench_number"        : bench_number,   # e.g. R-28
            "bench_member"        : bench_member,
            "status"              : status,
            "fetched_hearing_date": fetched_hearing_date,
        }

    else:
        logger.error(f"Unknown task_type: {task_type}")
        return None


def _split_bench(bench_raw: str) -> tuple:
    """
    Splits raw bench string into (bench_name, bench_number).

    Example input : "एस. बी राजस्व मण्डल - एस. बी-6 (R-28)"
    Logic:
      1. Split on first ' - ' → take everything after it → "एस. बी-6 (R-28)"
      2. Extract text inside () → bench_number = "R-28"
      3. Remaining text before () → bench_name = "एस. बी-6"

    If no dash found   → full string used as bench_name
    If no bracket found → bench_number = None
    """
    # Step 1: split on ' - ' and take after
    if " - " in bench_raw:
        after_dash = bench_raw.split(" - ", 1)[1].strip()
    else:
        after_dash = bench_raw.strip()

    # Step 2 & 3: extract bracket content
    match = re.search(r"\(([^)]+)\)", after_dash)
    if match:
        bench_number = match.group(1).strip()
        bench_name   = after_dash[:match.start()].strip()
    else:
        bench_name   = after_dash
        bench_number = None

    return bench_name, bench_number


# ══════════════════════════════════════════════════════════════════════════════
# DB OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def _load_queued_tasks(task_type: str) -> list:
    """
    Loads all tasks from FetchQueue with status='queued' for the given task_type.
    Joins with Cases to get case_id.
    """
    conn = get_conn()
    try:
        cursor = conn.execute(
            """
            SELECT
                fq.task_id,
                fq.case_pk,
                fq.hearing_id,
                fq.retry_count,
                c.case_id
            FROM FetchQueue fq
            JOIN Cases c ON fq.case_pk = c.case_pk
            WHERE fq.status    = 'pending'
              AND fq.task_type = ?
            ORDER BY fq.task_id ASC
            """,
            (task_type,),
        )
        rows = cursor.fetchall()
        logger.debug(f"Loaded {len(rows)} queued tasks for task_type='{task_type}'.")
        return rows
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# HTML HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _extract_hidden(html: str, field: str) -> str:
    """Extracts a hidden input field value from HTML."""
    soup = BeautifulSoup(html, "html.parser")
    tag  = soup.find("input", {"name": field})
    return tag["value"] if tag else ""


def _extract_updatepanel(text: str) -> str | None:
    """Extracts the HTML content from the GCMS UpdatePanel response."""
    parts = text.split("|")
    for i in range(len(parts)):
        if parts[i] == "updatePanel":
            return parts[i + 2]
    return None


def _get_value(soup: BeautifulSoup, label: str) -> str | None:
    """Finds a table cell by its Hindi label and returns the next sibling cell's text."""
    target = soup.find("td", string=re.compile(label))
    if target:
        sibling = target.find_next_sibling("td")
        if sibling:
            return sibling.text.strip()
    return None