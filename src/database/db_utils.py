import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional


import json
import time

PROJECT_ROOT = Path(__file__).parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "database.db"

def get_connection():
    """Returns a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH,timeout=30)
    conn.row_factory = sqlite3.Row 
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def is_first_run()-> bool:
    conn= get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM calendar_shadow")
    count = cursor.fetchone()[0]
    conn.close()
    return count == 0    

def upsert_calendar_event(event_id, google_event_id, source, title, description,
                          start_time, end_time, original_start_time,
                          priority, flexibility_score, status):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO calendar_shadow
            (event_id, google_event_id, source, title, description,
             start_time, end_time, original_start_time,
             priority, flexibility_score, status, is_triaged, last_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
                google_event_id = excluded.google_event_id,
                title           = excluded.title,
                description     = excluded.description,
                start_time      = excluded.start_time,
                end_time        = excluded.end_time,
                last_synced     = CURRENT_TIMESTAMP
        """, (event_id, google_event_id, source, title, description,
              start_time, end_time, original_start_time,
              priority, flexibility_score, status))
        conn.commit()
    finally:
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
    
def log_audit_action_conn(conn, event_id: str, change_type: str, reasoning_statement: str):
    """Internal version — reuses an existing connection. Use inside transactions."""
    conn.cursor().execute(
        "INSERT INTO audit_log (event_id, change_type, reasoning_statement) VALUES (?, ?, ?)",
        (event_id, change_type, reasoning_statement),
    )

def get_task_core_fields(task_id: str) -> dict | None:
    """
    Returns just the fields we watch for changes: priority and deadline.
    Returns None if the task doesn't exist yet.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT numeric_priority, deadline FROM task_metadata WHERE task_id = ?",
        (task_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_task_critical_fields(task_id: str, numeric_priority: int, deadline: str):
    """
    Updates only priority and deadline when Jira signals a change.
    Also resets effort_needs_triage=1 so the Triage Agent reprocesses it.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE task_metadata
        SET numeric_priority     = ?,
            deadline             = ?,
            effort_needs_triage  = 1
        WHERE task_id = ?
    ''', (numeric_priority, deadline, task_id))

    #reset triage state so Triage + Planner reprocess the slot
    cursor.execute('''
        UPDATE calendar_shadow
        SET priority   = ?,
            status     = 'Pending_Triage',
            is_triaged =0
        WHERE event_id = ?
    ''', (numeric_priority, task_id))

    conn.commit()
    conn.close()
    
def event_exists(event_id: str) -> bool:
    """Returns True if event_id already exists in calendar_shadow."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM calendar_shadow WHERE event_id = ?",
        (event_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None




def create_hitl_request(event_id, title, priority, effort, deadline, reason, options):
    """Inserts a new HITL request, returns its ID."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO hitl_pending
            (event_id, task_title, task_priority, task_effort, task_deadline,
             reason, options_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            event_id, title, priority, effort, deadline,
            reason, json.dumps(options),
        ))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_hitl_request(request_id):
    """Fetches a HITL request by ID."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM hitl_pending WHERE id = ?", (request_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def submit_hitl_decision(request_id, chosen_key):
    """Records the user's choice. Frontend calls this."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE hitl_pending
            SET status      = 'answered',
                chosen_key  = ?,
                answered_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
        """, (chosen_key, request_id))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_all_pending_hitl():
    """Returns all pending HITL requests for the frontend."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM hitl_pending
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def wait_for_hitl_decision(request_id, timeout_sec=600, poll_interval=2):
    """Polls until decision is made or timeout. Returns chosen_key or None."""
    elapsed = 0
    while elapsed < timeout_sec:
        req = get_hitl_request(request_id)
        if not req:
            return None
        if req["status"] == "answered":
            return req["chosen_key"]
        time.sleep(poll_interval)
        elapsed += poll_interval
    
    # Timeout — mark as expired
    conn = get_connection()
    try:
        conn.cursor().execute(
            "UPDATE hitl_pending SET status = 'expired' WHERE id = ?",
            (request_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return None