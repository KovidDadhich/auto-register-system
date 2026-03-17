# test_queue_builder_live.py

import sqlite3
from datetime import date
from config.settings import DB_PATH
from backend.queue.queue_builder import (
    _create_fetch_bench_tasks,
    _create_fetch_next_date_tasks,
)

today = date.today().isoformat()

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print(f"\nUsing database: {DB_PATH}")
print(f"Testing for date: {today}")

# ── Step 1: Insert a dummy case ───────────────────────────────────────────────
conn.execute("""
    INSERT INTO Cases (case_id, case_name)
    VALUES ('2015/4566', 'Sarkar - Jaitram')
""")
conn.commit()
dummy_case_pk = conn.execute(
    "SELECT case_pk FROM Cases WHERE case_id = '2015/4566'"
).fetchone()["case_pk"]
print(f"Inserted dummy case with case_pk: {dummy_case_pk}")

# ── Step 2: Insert dummy hearing rows due today ───────────────────────────────
conn.execute("""
    INSERT INTO Hearings (case_pk, bench_fetch_at, next_date_fetch_at)
    VALUES (?, ?, ?)
""", (dummy_case_pk, today, today))
conn.commit()
dummy_hearing_id = conn.execute(
    "SELECT hearing_id FROM Hearings WHERE case_pk = ?", (dummy_case_pk,)
).fetchone()["hearing_id"]
print(f"Inserted dummy hearing with hearing_id: {dummy_hearing_id}")

# ── Step 3: Run queue builder
print("\nRunning queue builder...")
bench_count     = _create_fetch_bench_tasks(conn, today)
next_date_count = _create_fetch_next_date_tasks(conn, today)
conn.commit()

print(f"fetch_bench tasks created    : {bench_count}")
print(f"fetch_next_date tasks created: {next_date_count}")

# ── Step 4: Show created tasks ────────────────────────────────────────────────
tasks = conn.execute("""
    SELECT * FROM FetchQueue WHERE case_pk = ?
""", (dummy_case_pk,)).fetchall()

print(f"\nTasks in FetchQueue for dummy case:")
for t in tasks:
    print(f"  task_id={t['task_id']} | type={t['task_type']} | status={t['status']} | date={t['scheduled_date']}")

# # ── Step 5: Cleanup ───────────────────────────────────────────────────────────
# print("\nCleaning up dummy data...")
# conn.execute("DELETE FROM FetchQueue WHERE case_pk = ?", (dummy_case_pk,))
# conn.execute("DELETE FROM Hearings WHERE case_pk = ?", (dummy_case_pk,))
# conn.execute("DELETE FROM Cases WHERE case_pk = ?", (dummy_case_pk,))
# conn.commit()
# print("Cleanup done. Database restored to original state.")

conn.close()