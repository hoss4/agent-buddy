from datetime import datetime, timedelta, timezone

from src.database.db_utils import get_connection, log_audit_action_conn

# CAIRO_OFFSET = timezone(timedelta(hours=2))


def to_naive(dt_str: str) -> datetime:
    if not dt_str:
        return None
    try:
        naive = dt_str.split("+")[0].split("Z")[0]
        return datetime.fromisoformat(naive)
    except Exception:
        return None


def get_effort(state: dict) -> int:
    
    triage = state.get("triage_decision") or {}
    if triage.get("estimated_effort_minutes"):
        print("getting time estimate from triage")
        return triage["estimated_effort_minutes"]

    effort = state["current_signal"].get("total_estimated_effort")
    if effort:
        print("getting time estimate from signal info")
        return effort

    return 60

# gets tasks that hasn't started , ends before deadline and has enough time  
def get_candidates(failed_task: dict, effort: int) -> list[dict]:
    
    now  = datetime.now().replace(microsecond=0)
    
    deadline = failed_task.get("deadline")
    deadline_dt = None
    
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline).replace(hour=18, minute=0)
        except Exception:
            pass

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT event_id, title, start_time, end_time, priority,
                flexibility_score, source
            FROM calendar_shadow
            WHERE status = 'Scheduled'
            AND event_id != ?
            AND start_time IS NOT NULL
            AND end_time   IS NOT NULL
            ORDER BY start_time ASC
        """, (failed_task["event_id"],))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    valid = []
    for row in rows:
        print("row : ",row)
        busy_slot_start = to_naive(row["start_time"])
        busy_slot_end = to_naive(row["end_time"])
        if not busy_slot_start or not busy_slot_end or busy_slot_start <= now:
            continue
        if deadline_dt and busy_slot_end > deadline_dt:
            continue
        duration = (busy_slot_end - busy_slot_start).total_seconds() / 60
        if duration < effort:
            continue
        valid.append(row)
        print("valid : ",valid)
    return valid


def best_match_lower_flex(candidates: list[dict], failed_priority:int ) -> dict | None:
    filtered=[]
    for candidate in candidates:
        if candidate["flexibility_score"]==1 and candidate["priority"] < failed_priority :
            filtered.append(candidate) 
    if not filtered:
        return None
    print(filtered)
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]

def best_match_higher_flex(candidates: list[dict], failed_priority: int) -> dict | None:
    filtered = []
    for candidate in candidates:
        if candidate["flexibility_score"] == 1 and candidate["priority"] >= failed_priority:
            filtered.append(candidate)
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


def best_match_lower_fixed(candidates: list[dict], failed_priority: int) -> dict | None:
    filtered = []
    for candidate in candidates:
        if candidate["flexibility_score"] == 0 and candidate["priority"] < failed_priority:
            filtered.append(candidate)
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


def best_match(candidates: list[dict], predicate) -> dict | None:
    filtered = [c for c in candidates if predicate(c)]
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


def get_failed_priority(state: dict) -> int:
    
    triage = state.get("triage_decision") or {}
    print("triage : ", triage)
    if triage.get("priority"):
        print("taking triage priority : ", triage.get("priority"))
        return triage["priority"]
    print("taking signal priority : ",state["current_signal"].get("priority", 5))
    return state["current_signal"].get("priority", 5)

def apply_auto_swap(failed_event_id: str, displaced: dict) -> dict:
    """Mark displaced as Pending_Triage, slot failed task into its place."""
    freed_start = displaced["start_time"]
    freed_end   = displaced["end_time"]

    conn = get_connection()
    try:
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE calendar_shadow
            SET status     = 'Pending_Triage',
                is_triaged = 0,
                start_time = NULL,
                end_time   = NULL
            WHERE event_id = ?
        """, (displaced["event_id"],))

        log_audit_action_conn(
            conn, displaced["event_id"], "Displaced",
            f"Auto-displaced to make room for higher-priority task.",
        )

        cursor.execute("""
            UPDATE calendar_shadow
            SET start_time = ?,
                end_time   = ?,
                status     = 'Scheduled'
            WHERE event_id = ?
        """, (freed_start, freed_end, failed_event_id))

        log_audit_action_conn(
            conn, failed_event_id, "Rescheduled",
            f"Auto-swap: displaced '{displaced['title']}' (priority {displaced['priority']}).",
        )

        conn.commit()
    finally:
        conn.close()

    return {"start": freed_start, "end": freed_end}


def resolver_node(state: dict) -> dict:
    failed_task = state["current_signal"]
    effort      = get_effort(state)

    print(f"  [resolver] Failed to schedule : '{failed_task['title']}' ")
    print(f"(priority : {failed_task.get('priority')}, effort : {effort} min)")

    
    candidates = get_candidates(failed_task, effort)
    print(f"  [resolver] Found {len(candidates)} eligible candidate slot(s) within deadline.")

    if not candidates:
        print(f"  [resolver] No candidates available — pure HITL.")
        return {
            **state,
            "resolver_outcome": "no_candidates",
            "swap_candidate":   None,
            "hitl_candidates":  None,
        }


  
    failed_priority = get_failed_priority(state)
    print("failed tasks priority : ", failed_priority)

    # attempt 1: Auto-swap if flexible and lower priority
    # ideal = best_match(
    #     candidates,
    #     lambda c: c["flexibility_score"] == 1 and c["priority"] < failed_priority,
    # )
    
    ideal = best_match_lower_flex(candidates,failed_priority)
    
    
    if ideal:
        freed = apply_auto_swap(failed_task["event_id"], ideal)
        print(f"  [resolver] Auto-swap: displaced '{ideal['title']}' "
              f"(priority {ideal['priority']}, Flexible)")
        return {
            **state,
            "resolver_outcome": "auto_swap",
            "swap_candidate":   ideal,
            "proposed_slot": {
                "proposed_start": freed["start"],
                "proposed_end":   freed["end"],
                "reasoning":      f"Auto-swapped with '{ideal['title']}'",
            },
            "calendar_push": {
                "title":       f"Deep Work: {failed_task['title']}",
                "description": failed_task.get("description", ""),
                "start":       freed["start"],
                "end":         freed["end"],
            },
        }

    # attempt 2: HITL with options
    # higher_flex = best_match(
    #     candidates,
    #     lambda c: c["flexibility_score"] == 1 and c["priority"] >= failed_priority,
    # )
    
    higher_flex = best_match_higher_flex(candidates,failed_priority)
    
    # lower_fixed = best_match(
    #     candidates,
    #     lambda c: c["flexibility_score"] == 0 and c["priority"] < failed_priority,
    # )

    lower_fixed = best_match_lower_fixed(candidates,failed_priority)
    
    hitl_options = []
    if higher_flex:
        hitl_options.append({"task": higher_flex, "label": "higher_priority_flexible"})
    if lower_fixed:
        hitl_options.append({"task": lower_fixed, "label": "lower_priority_fixed"})

    print(f"  [resolver] Tier 2 — HITL with {len(hitl_options)} candidate option(s).")
    return {
        **state,
        "resolver_outcome": "needs_hitl",
        "swap_candidate":   None,
        "hitl_candidates":  hitl_options,
    }