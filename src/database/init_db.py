import sqlite3
import os
from pathlib import Path

# Set path relative to the project root
PROJECT_ROOT = Path(__file__).parent.parent.parent
print(PROJECT_ROOT)
DB_PATH = PROJECT_ROOT / "data" / "database.db"

def init_db():
    """Initializes the SQLite database with the Agent Buddy schema."""
    
    # Ensure the data directory exists
    os.makedirs(PROJECT_ROOT / "data", exist_ok=True)

    # Connect to (or create) the database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")

    print(f"Initializing database at: {DB_PATH}")

    # 1. calendar_shadow: Active Planning Zone
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calendar_shadow (
            event_id TEXT PRIMARY KEY,
            google_event_id  TEXT,  
            source TEXT NOT NULL,
            title TEXT,
            description TEXT,
            start_time DATETIME,
            end_time DATETIME,
            original_start_time DATETIME,
            priority INTEGER DEFAULT 5,
            flexibility_score INTEGER DEFAULT 1, -- 0: Fixed, 1: Flexible
            status TEXT DEFAULT 'Tentative',
            is_triaged INTEGER DEFAULT 0,
            last_synced DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 2. task_metadata: Master Work Record
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_metadata (
            task_id TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            remaining_effort INTEGER,
            deadline DATETIME,
            dependency_id TEXT,
            status TEXT,
            issue_type  TEXT DEFAULT 'Task',      
            total_estimated_effort  INTEGER DEFAULT 0,  
            numeric_priority        INTEGER DEFAULT 5,       
            effort_needs_triage     INTEGER DEFAULT 1        
        )
    ''')

    # 3. history_ledger: The Archive
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            title TEXT,
            source TEXT,
            category TEXT,
            actual_start DATETIME,
            actual_end DATETIME,
            was_skipped BOOLEAN DEFAULT 0,
            user_feedback TEXT
        )
    ''')

    # 4. audit_log: Reasoning & Action Log
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            event_id TEXT,
            change_type TEXT, -- e.g., 'Rescheduled', 'Created'
            reasoning_statement TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS hitl_pending (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id        TEXT NOT NULL,           -- the failed task being decided on
            task_title      TEXT,
            task_priority   INTEGER,
            task_effort     INTEGER,
            task_deadline   TEXT,
            reason          TEXT,                    -- why HITL was triggered
            options_json    TEXT NOT NULL,           -- JSON-encoded list of options
            status          TEXT DEFAULT 'pending',  -- pending | answered | expired
            chosen_key      TEXT,                    -- "A", "B", "C"...
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            answered_at     DATETIME
        )
    ''')

    conn.commit()
    conn.close()
    print("Database tables created successfully.")

if __name__ == "__main__":
    init_db()