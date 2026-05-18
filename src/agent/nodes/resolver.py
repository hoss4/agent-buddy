from datetime import datetime, timedelta, timezone

from src.database.db_utils import get_connection, log_audit_action_conn

CAIRO_OFFSET = timezone(timedelta(hours=2))


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
        return triage["estimated_effort_minutes"]
    return state["current_signal"].get("total_estimated_effort") or 60


def get_candidates(failed_task: dict, effort: int) -> list[dict]:
    """
    All scheduled Jira tasks (excluding the failed one) whose slot:
    - is in the future
    - ends before failed task's deadline
    - is at least `effort` minutes long
    """
    now = datetime.now(CAIRO_OFFSET).replace(tzinfo=None)
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
            WHERE status            = 'Scheduled'
            AND source            = 'Jira'
            AND event_id         != ?
            AND start_time IS NOT NULL
            AND end_time   IS NOT NULL
            ORDER BY start_time ASC
        """, (failed_task["event_id"],))
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    valid = []
    for r in rows:
        bs = to_naive(r["start_time"])
        be = to_naive(r["end_time"])
        if not bs or not be or bs <= now:
            continue
        if deadline_dt and be > deadline_dt:
            continue
        duration = (be - bs).total_seconds() / 60
        if duration < effort:
            continue
        valid.append(r)
    return valid


def best_match(candidates: list[dict], predicate) -> dict | None:
    filtered = [c for c in candidates if predicate(c)]
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


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

    print(f"  [resolver] Failed to schedule '{failed_task['title']}' "
          f"(priority {failed_task.get('priority', 5)}, effort {effort}min)")

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

    failed_priority = failed_task.get("priority", 5)

    # Tier 1: Auto-swap (Flexible + lower priority)
    ideal = best_match(
        candidates,
        lambda c: c["flexibility_score"] == 1 and c["priority"] < failed_priority,
    )
    if ideal:
        freed = apply_auto_swap(failed_task["event_id"], ideal)
        print(f"  [resolver] ✅ Auto-swap: displaced '{ideal['title']}' "
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

    # Tier 2: HITL with options
    higher_flex = best_match(
        candidates,
        lambda c: c["flexibility_score"] == 1 and c["priority"] >= failed_priority,
    )
    lower_fixed = best_match(
        candidates,
        lambda c: c["flexibility_score"] == 0 and c["priority"] < failed_priority,
    )

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