from src.agent.state import AgentState
from src.database.db_utils import get_connection
from datetime import datetime, timezone, timedelta


def to_utc(dt_str: str) -> datetime:
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _get_conflicting_events(start: str, end: str, exclude_event_id: str) -> list[dict]:
    proposed_start = to_utc(start)
    proposed_end   = to_utc(end)

    if not proposed_start or not proposed_end:
        return []

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, title, start_time, end_time, flexibility_score, status
            FROM calendar_shadow
            WHERE status NOT IN ('Completed', 'Dismissed', 'Pending_Triage')
              AND event_id != ?
              AND start_time IS NOT NULL
              AND end_time   IS NOT NULL
        """, (exclude_event_id,))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    conflicts = []
    for row in rows:
        event_start = to_utc(row["start_time"])
        event_end   = to_utc(row["end_time"])

        if not event_start or not event_end:
            continue

        if proposed_start < event_end and proposed_end > event_start:
            conflicts.append(row)

    return conflicts


def get_conflicting_events(start: str, end: str, exclude_event_id: str) -> list[dict]:
    """
    Returns all events that overlap with the proposed slot.
    Excludes the task being scheduled itself.
    """
    conn = get_connection()
    proposed_start = to_utc(start)
    proposed_end   = to_utc(end)
    
    try:
        cursor = conn.cursor()
        
        
        cursor.execute("""
            SELECT event_id, title, start_time, end_time, flexibility_score, status
            FROM calendar_shadow
            WHERE start_time < ?
              AND end_time   > ?
              AND status NOT IN ('Completed', 'Dismissed', 'Pending_Triage')
              AND event_id  != ?
        """, (end, start, exclude_event_id))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def auditor_node(state: AgentState) -> AgentState:
    """
    Checks the proposed slot for conflicts.
    Sets conflict_found, conflicting_event in state.
    """
    proposed   = state.get("proposed_slot")
    signal     = state["current_signal"]

    if not proposed or not proposed.get("proposed_start") or not proposed.get("proposed_end"):
        # give up go to hitl
        print("  [auditor] No proposed slot to check.")
        print("proposed : ",proposed)
        return {**state, "conflict_found": False, "conflicting_event": None}

    start    = proposed["proposed_start"]
    end      = proposed["proposed_end"]
    event_id = signal["event_id"]

    conflicts = get_conflicting_events(start, end, event_id)

    if not conflicts:
        print(f"  [auditor]  Slot is free: {start} → {end}")
        return {**state, "conflict_found": False, "conflicting_event": None}

    # Report the most problematic conflict (Fixed > Flexible)
    fixed_conflicts    = [c for c in conflicts if c["flexibility_score"] == 0]
    flexible_conflicts = [c for c in conflicts if c["flexibility_score"] == 1]

    if fixed_conflicts:
        worst = fixed_conflicts[0]
        print(f"  [auditor]  Fixed conflict: '{worst['title']}' at {worst['start_time']}")
    else:
        worst = flexible_conflicts[0]
        print(f"  [auditor]  Flexible conflict: '{worst['title']}' at {worst['start_time']}")

    return {
        **state,
        "conflict_found":    True,
        "conflicting_event": worst,
    }