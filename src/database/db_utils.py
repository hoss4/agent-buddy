import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional

# Locate the database dynamically
PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "database.db"

def get_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # Allows dict-like row access
    return conn



def upsert_calendar_event(
    event_id: str,
    source: str,
    title: str,
    description: str,
    start_time: str,
    end_time: str,
    original_start_time: str,
    priority: int = 5,
    flexibility_score: int = 1,
    status: str = "Tentative",
):
    """Inserts a new event or updates an existing one in calendar_shadow."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO calendar_shadow 
        (event_id, source, title, description, start_time, end_time,
         original_start_time, priority, flexibility_score, status, last_synced)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(event_id) DO UPDATE SET
            title               = excluded.title,
            description         = excluded.description,
            start_time          = excluded.start_time,
            end_time            = excluded.end_time,
            status              = excluded.status,
            last_synced         = CURRENT_TIMESTAMP
        """,
        (
            event_id, source, title, description, start_time, end_time,
            original_start_time, priority, flexibility_score, status,
        ),
    )
    conn.commit()
    conn.close()

def upsert_task_metadata(
    task_id: str,
    title: str,
    description: str,
    total_estimated_effort: int,
    remaining_effort: int,
    deadline: str,
    issue_type: str = "Task",
    numeric_priority: int = 5,
    dependency_id: str = None,
    status: str = "Open",
):
    effort_needs_triage = 1 if total_estimated_effort == 0 else 0

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO task_metadata
        (task_id, title, description, issue_type, total_estimated_effort,
         remaining_effort, deadline, dependency_id, status,
         numeric_priority, effort_needs_triage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_id) DO UPDATE SET
            title                  = excluded.title,
            description            = excluded.description,
            issue_type             = excluded.issue_type,
            remaining_effort       = excluded.remaining_effort,
            deadline               = excluded.deadline,
            status                 = excluded.status,
            numeric_priority       = excluded.numeric_priority,
            effort_needs_triage    = excluded.effort_needs_triage
    ''', (
        task_id, title, description, issue_type,
        total_estimated_effort, remaining_effort,
        deadline, dependency_id, status,
        numeric_priority, effort_needs_triage,
    ))
    conn.commit()
    conn.close()

def log_audit_action(
    event_id: str,
    change_type: str,
    reasoning_statement: str,
):
    """
    Records every autonomous agent decision into the audit_log.
    
    change_type options: 'Created', 'Rescheduled', 'Conflict_Flagged', 'Deleted'
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO audit_log (event_id, change_type, reasoning_statement)
        VALUES (?, ?, ?)
        """,
        (event_id, change_type, reasoning_statement),
    )
    conn.commit()
    conn.close()