# Current Goal

Implement retry mechanism for failed scraper jobs.

---

# Current Problem

Some failed jobs are getting duplicated.

---

# Relevant Files

* scraper_worker.py
* queue_builder.py
* db_writer.py

---

# Expected Behavior

Failed jobs should retry maximum 3 times.
No duplicate queue entries.
