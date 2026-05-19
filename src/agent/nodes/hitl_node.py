from src.database.db_utils import get_connection, log_audit_action_conn


def apply_swap(failed_event_id: str, displaced: dict) -> dict:
    freed_start = displaced["start_time"]
    freed_end = displaced["end_time"]
    source = displaced.get("source", "Jira")
    google_id = displaced.get("google_event_id")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        if source == "Jira":
            cursor.execute("""
                UPDATE calendar_shadow
                SET status     = 'Pending_Triage',
                    is_triaged = 0,
                    start_time = NULL,
                    end_time   = NULL,
                    google_event_id = NULL
                WHERE event_id = ?
            """, (displaced["event_id"],))
            log_audit_action_conn(
                conn, displaced["event_id"], "Displaced",
                f"HITL-approved displacement.",
            )
        else:
            cursor.execute("""
                UPDATE calendar_shadow
                SET status = 'Dismissed',
                    google_event_id = NULL
                WHERE event_id = ?
            """, (displaced["event_id"],))
            
            
        cursor.execute("""
            UPDATE calendar_shadow
            SET start_time = ?,
                end_time   = ?,
                status     = 'Scheduled'
            WHERE event_id = ?
        """, (freed_start, freed_end, failed_event_id))
        
        log_audit_action_conn(
            conn, failed_event_id, "Rescheduled",
            f"HITL swap: displaced '{displaced['title']}'.",
        )
        
        conn.commit()
    finally:
        conn.close()
    return {"start": freed_start, "end": freed_end, "google_event_id": google_id}


def hitl_node(state: dict) -> dict:
    signal     = state["current_signal"]
    candidates = state.get("hitl_candidates") or []

    print("\n" + "=" * 60)
    print("  ⚠  AGENT BUDDY — HUMAN DECISION REQUIRED")
    print("=" * 60)
    print(f"\n  Task: {signal['title']} (priority {signal.get('priority', 5)})")
    print(f"  Effort: {signal.get('total_estimated_effort', '?')} min "
          f"| Deadline: {signal.get('deadline', 'None')}")

    if state.get("resolver_outcome") == "no_candidates":
        print(f"  Reason: No tasks in the deadline window can be displaced.")
    else:
        print(f"  Reason: Scheduler found no free slot. Possible swap options:")

    options       = {}
    options_text  = {}
    next_key      = "A"

    for cand in candidates:
        task = cand["task"]
        flex_str  = "Flexible" if task["flexibility_score"] == 1 else "Fixed"
        desc = (f"Swap with '{task['title']}' "
                f"(priority {task['priority']}, {flex_str}, "
                f"{task['start_time']} → {task['end_time']})")
        options[next_key]      = ("swap", task)
        options_text[next_key] = desc
        next_key = chr(ord(next_key) + 1)

    options[next_key]      = ("skip",    None)
    options_text[next_key] = "Skip — leave unscheduled, try next cycle"
    next_key = chr(ord(next_key) + 1)
    options[next_key]      = ("discard", None)
    options_text[next_key] = "Discard this task entirely"

    print(f"\n  Options:")
    for key, desc in options_text.items():
        print(f"  [{key}] {desc}")
    print()

    while True:
        choice = input(f"  Your choice ({'/'.join(options.keys())}): ").strip().upper()
        if choice in options:
            break
        print(f"  Invalid choice.")

    action, displaced = options[choice]
    calendar_push = None
    delete=None

    if action == "swap" and displaced:
        freed = apply_swap(signal["event_id"], displaced)
        
        print(f"\n  [hitl]  Swap applied: '{displaced['title']}' displaced, "
              f"'{signal['title']}' scheduled at {freed['start']}.")
        
        
        if freed.get("google_event_id"):
            delete = freed["google_event_id"]
        
        calendar_push = {
            "title":       f"Deep Work: {signal['title']}",
            "description": signal.get("description", ""),
            "start":       freed["start"],
            "end":         freed["end"],
        }
    else:
        new_status = "Dismissed" if action == "discard" else "HITL_Resolved"
        conn = get_connection()
        try:
            conn.cursor().execute("""
                UPDATE calendar_shadow
                SET status = ?
                WHERE event_id = ?
            """, (new_status, signal["event_id"]))
            log_audit_action_conn(
                conn, signal["event_id"], "Conflict_Flagged",
                f"HITL decision: {action}",
            )
            conn.commit()
        finally:
            conn.close()
        print(f"\n  [hitl] Decision recorded: {action}")

    print("=" * 60 + "\n")
    return {**state, "hitl_decision": action, "calendar_push": calendar_push, "calendar_delete": delete}