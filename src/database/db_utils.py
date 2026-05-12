import sqlite3
from pathlib import Path

# Locate the database dynamically
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "database.db"

def get_connection():
    """Returns a connection to the SQLite database."""
    return sqlite3.connect(DB_PATH)

def upsert_calendar_event(event_id: str, source: str, title: str, description: str, 
                          start_time: str, end_time: str, original_start_time: str, 
                          priority: int = 5, flexibility_score: int = 1, status: str = 'Tentative'):
    """Inserts a new event or updates an existing one in the calendar_shadow table."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Using SQLite's ON CONFLICT for seamless Upserts
    cursor.execute('''
        INSERT INTO calendar_shadow 
        (event_id, source, title, description, start_time, end_time, original_start_time, priority, flexibility_score, status, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(event_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            start_time=excluded.start_time,
            end_time=excluded.end_time,
            status=excluded.status,
            last_synced=CURRENT_TIMESTAMP
    ''', (event_id, source, title, description, start_time, end_time, original_start_time, priority, flexibility_score, status))
    
    conn.commit()
    conn.close()

def upsert_task_metadata(task_id: str, title: str, description: str, 
                         total_estimated_effort: int, remaining_effort: int, 
                         deadline: str, dependency_id: str = None, status: str = 'Open'):
    """Inserts or updates a Jira task record in the task_metadata table."""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO task_metadata 
        (task_id, title, description, total_estimated_effort, remaining_effort, deadline, dependency_id, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            remaining_effort=excluded.remaining_effort,
            deadline=excluded.deadline,
            status=excluded.status
    ''', (task_id, title, description, total_estimated_effort, remaining_effort, deadline, dependency_id, status))
    
    conn.commit()
    conn.close()