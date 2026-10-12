import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

DB_FILE = Path(os.getenv("DB_FILE", str(Path(__file__).resolve().with_name("system_hub.db"))))

def init_db():
    """Initialize persistent sleep and local calendar tables."""
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL,
                sleep_start_time TEXT,
                last_updated TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sleep_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sleep_start TEXT NOT NULL,
                wake_time TEXT NOT NULL,
                duration_hours REAL NOT NULL,
                deficit_hours REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                category TEXT DEFAULT 'general',
                description TEXT,
                is_secret INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS network_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                online INTEGER NOT NULL,
                targets_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO sleep_state (id, state, sleep_start_time, last_updated) "
            "VALUES (1, 'AWAKE', NULL, ?)",
            (datetime.now().isoformat(),),
        )

@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()