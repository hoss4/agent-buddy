from src.agent.state import AgentState
from src.database.db_utils import get_connection


def get_conflicting_events(start: str, end: str, exclude_event_id: str) -> list[dict]:
    """
    Returns all events that overlap with the proposed slot.
    Excludes the task being scheduled itself.
    """
    conn = get_connection()
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

    if not proposed:
        print("  [auditor] No proposed slot to check.")
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