# PROJECT MAP

## scheduler.py

Purpose:
Runs scheduled tasks.

Connected Files:

* queue_builder.py

---

## queue_builder.py

Purpose:
Creates scraping jobs.

Connected Files:

* scraper_worker.py
* database.py

---

## scraper_worker.py

Purpose:
Runs selenium scraper.

Connected Files:

* parser.py
* db_writer.py

---

## db_writer.py

Purpose:
Stores data into SQLite.

Connected Files:

* database.py
