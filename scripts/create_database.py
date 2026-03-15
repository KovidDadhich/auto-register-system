import sqlite3
from config.settings import DATABASE_PATH # Path in string data type
from pathlib import Path



def create_DB():
    # database path - not string path
    DB_PATH = Path(DATABASE_PATH)
    # ensure database folder exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # connect to database
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # -------------------------
    # Cases Table
    # -------------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Cases (
        case_pk INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT UNIQUE NOT NULL,
        case_name TEXT,
        district TEXT,
        prakaran TEXT,
        adhiniyam TEXT,
        old_case_id TEXT
    )
    """)

    # -------------------------
    # Hearings Table
    # -------------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Hearings (
        hearing_id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_pk INTEGER,
        prev_hearing_date TEXT,
        current_hearing_date TEXT,
        bench_fetch_at TEXT,
        next_date_fetch_at TEXT,
        bench_name_number TEXT,
        bench_member TEXT,
        status TEXT,
        comments TEXT,
        next_hearing_date TEXT,
        FOREIGN KEY(case_pk) REFERENCES Cases(case_pk)
    )
    """)

    # -------------------------
    # FetchQueue Table
    # -------------------------
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS FetchQueue (
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_pk INTEGER,
        hearing_id INTEGER,
        task_type TEXT,
        scheduled_date TEXT,
        status TEXT,
        retry_count INTEGER DEFAULT 0
    )
    """)

    # -------------------------
    # Indexes
    # -------------------------

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_case_id ON Cases(case_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hearings_case_pk ON Hearings(case_pk)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_hearing_date ON Hearings(current_hearing_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_date ON FetchQueue(scheduled_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON FetchQueue(status)")


    # commit changes
    conn.commit()
    conn.close()

    print("Database and tables created successfully.")