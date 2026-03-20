import sqlite3
import os
import logging
from config.settings import DB_PATH

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Path setup ─────────────────────────────────────────────────────────────────
# BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# DB_PATH  = os.path.join(BASE_DIR, "database", "register.db")


def get_connection():
    """Return a connection to the SQLite database."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")   # enforce FK constraints
    conn.execute("PRAGMA journal_mode = WAL")  # better concurrency
    return conn


def create_tables(conn):
    cursor = conn.cursor()

    # ── Table A: Cases (Static) ────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Cases (
            case_pk     INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id     TEXT    UNIQUE NOT NULL,
            case_name   TEXT,
            district    TEXT,
            prakaran    TEXT,
            adhiniyam   TEXT,
            old_case_id TEXT
        )
    """)
    log.info("Table 'Cases' created (or already exists).")

    # ── Table B: Hearings (Dynamic) ────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS Hearings (
            hearing_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            case_pk               INTEGER NOT NULL,
            prev_hearing_date     DATE,
            current_hearing_date  DATE,
            bench_fetch_at        DATE,
            next_date_fetch_at    DATE,
            bench_name            TEXT,
            bench_number          TEXT,
            bench_member          TEXT,
            status                TEXT,
            comments              TEXT,
            next_hearing_date     DATE,
            hearing_date_changed  TEXT,
            bench_fetched         INTEGER DEFAULT 0,
            next_date_fetched     INTEGER DEFAULT 0,
            system_note           TEXT,
            updated_at            TEXT    DEFAULT CURRENT_TIMESTAMP,
            last_synced_at        TEXT,
            FOREIGN KEY (case_pk) REFERENCES Cases(case_pk)
        )
    """)
    log.info("Table 'Hearings' created (or already exists).")

    # ── Table C: FetchQueue ────────────────────────────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS FetchQueue (
            task_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            case_pk        INTEGER,
            hearing_id     INTEGER,
            task_type      TEXT,
            scheduled_date DATE,
            status         TEXT     DEFAULT 'queued',
            retry_count    INTEGER  DEFAULT 0,
            last_error     TEXT,
            created_at     TEXT     DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_pk)    REFERENCES Cases(case_pk),
            FOREIGN KEY (hearing_id) REFERENCES Hearings(hearing_id)
        )
    """)
    log.info("Table 'FetchQueue' created (or already exists).")

    conn.commit()


def create_triggers(conn):
    """
    Creates SQLite triggers.
    updated_at trigger: auto-updates Hearings.updated_at on every UPDATE.
    This allows sheets_sync to detect which rows changed since last sync.
    """
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_hearings_updated_at
        AFTER UPDATE ON Hearings
        FOR EACH ROW
        WHEN OLD.updated_at = NEW.updated_at   -- only fire if updated_at not already changed
        BEGIN
            UPDATE Hearings
            SET updated_at = CURRENT_TIMESTAMP
            WHERE hearing_id = OLD.hearing_id;
        END
    """)
    conn.commit()
    log.info("Trigger 'trg_hearings_updated_at' created (or already exists).")


def create_indexes(conn):
    cursor = conn.cursor()

    indexes = [
        # Cases
        ("idx_cases_case_id",                "Cases",    "case_id"),
        # Hearings
        ("idx_hearings_case_pk",             "Hearings", "case_pk"),
        ("idx_hearings_current_hearing_date","Hearings", "current_hearing_date"),
        ("idx_hearings_updated_at",          "Hearings", "updated_at"),
        ("idx_hearings_last_synced_at",      "Hearings", "last_synced_at"),
        ("idx_hearings_bench_fetched",       "Hearings", "bench_fetched"),
        ("idx_hearings_next_date_fetched",   "Hearings", "next_date_fetched"),
        # FetchQueue
        ("idx_fetchqueue_scheduled_date",    "FetchQueue", "scheduled_date"),
        ("idx_fetchqueue_status",            "FetchQueue", "status"),
    ]

    for index_name, table, column in indexes:
        cursor.execute(f"""
            CREATE INDEX IF NOT EXISTS {index_name} ON {table}({column})
        """)
        log.info(f"Index '{index_name}' on {table}({column}) created (or already exists).")

    conn.commit()


def verify_schema(conn):
    """Print all tables, columns, triggers and indexes for a quick sanity check."""
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = cursor.fetchall()

    log.info("── Schema Verification ──────────────────────────")
    for (table_name,) in tables:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        col_names = [col[1] for col in columns]
        log.info(f"  {table_name}: {col_names}")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")
    indexes = cursor.fetchall()
    log.info(f"  Indexes: {[i[0] for i in indexes]}")

    cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name")
    triggers = cursor.fetchall()
    log.info(f"  Triggers: {[t[0] for t in triggers]}")
    log.info("─────────────────────────────────────────────────")


def main():
    log.info(f"Database path: {DB_PATH}")
    conn = get_connection()
    try:
        create_tables(conn)
        create_triggers(conn)
        create_indexes(conn)
        verify_schema(conn)
        log.info("Database setup complete.")
    except Exception as e:
        log.error(f"Database setup failed: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()