from src.database.db_utils import get_connection, log_audit_action_conn, create_hitl_request, wait_for_hitl_decision


def apply_swap(failed_event_id: str, displaced: dict) -> None:
    freed_start = displaced["start_time"]
    freed_end = displaced["end_time"]
    source = displaced.get("source", "Jira")
    #google_id = displaced.get("google_event_id")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        
        if source == "Jira":
            cursor.execute("""
                UPDATE calendar_shadow
                SET status     = 'Dismissed',
                    is_triaged = 1,
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
            conn, failed_event_id, "Scheduled",
            f"HITL swap: displaced '{displaced['title']}'.",
        )
        
        conn.commit()
    finally:
        conn.close()



def hitl_node(state: dict) -> dict:
    
    signal     = state["current_signal"]
    candidates = state.get("hitl_candidates") or []
    triage = state.get("triage_decision") or {}
    
    
    options_for_db = []
    options_lookup = {}    # key → (action, displaced_task)
    next_key = "A"
    
    if triage.get("priority"):
        
        print("taking triage priority : ", triage.get("priority"))
        triage_priority = triage.get("priority")
        
    else :
        triage_priority = signal.get('priority', 5)
    
    for cand in candidates:
        
        task = cand["task"]
        
        
        if task["flexibility_score"] == 1 :
            flex_str = "Flexible" 
        else:
            flex_str = "Fixed"
        
        desc = (f"Swap with '{task['title']}' "
                f"(priority {triage_priority}, {flex_str}, "
                f"{task['start_time']} → {task['end_time']})")
        
        options_for_db.append({
            "key": next_key,
            "action": "swap",
            "description": desc,
            "displaced_task_id": task["event_id"],
        })
        
        options_lookup[next_key] = ("swap", task)
        
        next_key = chr(ord(next_key) + 1)
        
    options_for_db.append({
        "key": next_key, "action": "skip", "description": "Skip — try next cycle",
    })
    options_lookup[next_key] = ("skip", None)
    next_key = chr(ord(next_key) + 1)

    options_for_db.append({
        "key": next_key, "action": "discard", "description": "Discard this task entirely",
    })
    
    options_lookup[next_key] = ("discard", None)

        # Determine reason
    if state.get("resolver_outcome") == "no_candidates":
        reason = "No tasks in the deadline window can be displaced."
    else:
        reason = "Scheduler found no free slot. Possible swap options."

    # Write request to DB
    request_id = create_hitl_request(
        event_id      = signal["event_id"],
        title         = signal["title"],
        priority      = triage_priority,
        effort        = signal.get("total_estimated_effort"),
        deadline      = signal.get("deadline"),
        reason        = reason,
        options       = options_for_db,
    )
    print(f"\n  [hitl] Created HITL request #{request_id}, waiting for user decision...")

    # Poll for decision
    chosen_key = wait_for_hitl_decision(request_id, timeout_sec=600)

    if not chosen_key:
        print(f"  [hitl]  Timeout — no decision received. Skipping task.")
        return {**state, "hitl_decision": "timeout",
                "calendar_push": None, "calendar_delete": None}

    print(f"  [hitl] User chose: {chosen_key}")
    action, displaced = options_lookup.get(chosen_key, ("skip", None))

    calendar_push = None
    delete = None

    if action == "swap" and displaced:
        apply_swap(signal["event_id"], displaced)
        if displaced.get("google_event_id"):
            delete = displaced["google_event_id"]
        calendar_push = {
            "title":       f"Deep Work: {signal['title']}",
            "description": signal.get("description", ""),
            "start":       displaced["start_time"],
            "end":         displaced["end_time"],
        }
        print(f"  [hitl] Swap applied: '{displaced['title']}' displaced.")
    else:
        new_status = "Dismissed" if action == "discard" else "HITL_Resolved"
        conn = get_connection()
        try:
            conn.cursor().execute("""
                UPDATE calendar_shadow SET status = ? WHERE event_id = ?
            """, (new_status, signal["event_id"]))
            log_audit_action_conn(
                conn, signal["event_id"], "Conflict_Flagged",
                f"HITL decision: {action}",
            )
            conn.commit()
        finally:
            conn.close()

    return {**state, "hitl_decision": action,
            "calendar_push": calendar_push, "calendar_delete": delete}


  
  
    
    # print("\n" + "=" * 60)
    # print("   AGENT BUDDY — HUMAN DECISION REQUIRED")
    # print("=" * 60)
    
    
    # print(f"\n  Task: {signal['title']} (priority {triage_priority})")
    # print(f"  Effort: {signal.get('total_estimated_effort', '?')} min "
    #       f"| Deadline: {signal.get('deadline', 'None')}")

    # if state.get("resolver_outcome") == "no_candidates":
    #     print(f"  Reason: No tasks in the deadline window can be displaced.")
    # else:
    #     print(f"  Reason: Scheduler found no free slot. Possible swap options:")

    # options       = {}
    # options_text  = {}
    # next_key      = "A"

    # for cand in candidates:
    #     task = cand["task"]
        
        
    #     if task["flexibility_score"] == 1 :
    #         flex_str  = "Flexible"    
    #     else :
    #         flex_str = "Fixed"
        
    #     desc = (f" Swap with '{task['title']}' (priority {task['priority']}, {flex_str}, {task['start_time']} → {task['end_time']})")
        
    #     options[next_key]      = ("swap", task)
    #     options_text[next_key] = desc
    #     next_key = chr(ord(next_key) + 1)

    # options[next_key]      = ("skip",    None)
    # options_text[next_key] = "Skip — leave unscheduled, try next cycle"
    # next_key = chr(ord(next_key) + 1)
    # options[next_key]      = ("discard", None)
    # options_text[next_key] = "Discard this task entirely"

    # print(f"\n  Options:")
    # for key, desc in options_text.items():
    #     print(f"  [{key}] {desc}")
    # print()

    # while True:
    #     choice = input(f"  Your choice ({'/'.join(options.keys())}): ").strip().upper()
    #     if choice in options:
    #         break
    #     print(f"  Invalid choice.")

    # action, displaced = options[choice]
    # calendar_push = None
    # delete=None

    # if action == "swap" and displaced:
    #     apply_swap(signal["event_id"], displaced)
        
    #     print(f"\n  [hitl]  Swap applied: '{displaced['title']}' displaced, "
    #           f"'{signal['title']}' scheduled at {displaced['start_time']}.")
        
        
    #     if displaced.get("google_event_id"):
    #         delete = displaced["google_event_id"]
        
    #     calendar_push = {
    #         "title":       f"Deep Work: {signal['title']}",
    #         "description": signal.get("description", ""),
    #         "start":       displaced["start_time"],
    #         "end":         displaced["end_time"],
    #     }
    # else:
    #     new_status = "Dismissed" if action == "discard" else "HITL_Resolved"
    #     conn = get_connection()
    #     try:
    #         conn.cursor().execute("""
    #             UPDATE calendar_shadow
    #             SET status = ?
    #             WHERE event_id = ?
    #         """, (new_status, signal["event_id"]))
    #         log_audit_action_conn(
    #             conn, signal["event_id"], "Conflict_Flagged",
    #             f"HITL decision: {action}",
    #         )
    #         conn.commit()
    #     finally:
    #         conn.close()
    #     print(f"\n  [hitl] Decision recorded: {action}")

    # print("=" * 60 + "\n")
    # return {**state, "hitl_decision": action, "calendar_push": calendar_push, "calendar_delete": delete}