# Online Auto Register System

## Purpose

Automates advocate office registration workflow.

---

# Tech Stack

* Python
* Flask
* SQLite
* Selenium
* APScheduler

---

# Main Workflow

Scheduler
→ Queue Builder
→ FetchQueue DB
→ Scraper Worker
→ DB Writer
→ SQLite Database

---

# Important Rules

## Cases Table

* case_id must be unique
* case_pk auto increment
* never manually insert case_pk

## Hearings Table

* one row per case per hearing date

---

# Modules

## scheduler.py

Handles scheduled jobs.

## queue_builder.py

Builds scraping queue.

## scraper_worker.py

Runs scraping jobs.

## db_writer.py

Writes data to database.

---

# Current Status

* Queue builder completed
* Scraper working
* Retry logic incomplete
* Catch-up scanner pending
