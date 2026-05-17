# src/agent/nodes/scheduler.py
from datetime import datetime, timedelta, timezone

from src.database.db_utils import get_connection


CAIRO_OFFSET = timezone(timedelta(hours=2))
WORK_START_HOUR = 9
WORK_END_HOUR   = 18
BUFFER_MIN      = 15
HORIZON_DAYS    = 14


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def to_naive(dt_str: str) -> datetime:
    """Treats all stored times as Cairo wall-clock — strips any timezone."""
    if not dt_str:
        return None
    try:
        naive = dt_str.split("+")[0].split("Z")[0]
        return datetime.fromisoformat(naive)
    except Exception:
        return None


def get_effort(state: dict) -> int:
    """Resolves task effort from triage decision, signal, or default."""
    triage = state.get("triage_decision") or {}
    if triage.get("estimated_effort_minutes"):
        return triage["estimated_effort_minutes"]

    effort = state["current_signal"].get("total_estimated_effort")
    if effort:
        return effort

    return 60


def get_busy_slots() -> list[dict]:
    """All scheduled events — same query as planner used."""
    now = datetime.now(CAIRO_OFFSET).replace(tzinfo=None)
    horizon = now + timedelta(days=HORIZON_DAYS)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title, start_time, end_time, flexibility_score
            FROM calendar_shadow
            WHERE status      = 'Scheduled'
              AND start_time IS NOT NULL
              AND end_time   IS NOT NULL
            ORDER BY start_time ASC
        """)
        rows = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    # Filter to events within our horizon (in naive Cairo time)
    in_window = []
    for r in rows:
        bs = to_naive(r["start_time"])
        if bs and now <= bs <= horizon:
            in_window.append(r)
    return in_window


# ─────────────────────────────────────────────────────────────────────────────
# CORE: FIND ALL VALID SLOTS
# ─────────────────────────────────────────────────────────────────────────────

def find_available_slots(busy: list[dict], effort: int,
                          start_search: datetime, deadline: datetime) -> list[dict]:
    """
    Returns every valid slot of `effort` minutes within working hours,
    Mon-Fri, between `start_search` and `deadline`, respecting buffers.
    """
    valid_slots = []

    # Normalize busy events to naive datetimes, sorted
    busy_intervals = []
    for slot in busy:
        bs = to_naive(slot["start_time"])
        be = to_naive(slot["end_time"])
        if bs and be:
            busy_intervals.append((bs, be, slot["title"]))
    busy_intervals.sort()

    current_day = start_search.replace(hour=0, minute=0, second=0, microsecond=0)
    end_day     = deadline.replace(hour=0, minute=0, second=0, microsecond=0)

    while current_day <= end_day:
        # Skip weekends (Saturday=5, Sunday=6)
        if current_day.weekday() >= 5:
            current_day += timedelta(days=1)
            continue

        day_start = current_day.replace(hour=WORK_START_HOUR, minute=0)
        day_end   = current_day.replace(hour=WORK_END_HOUR,   minute=0)

        # Cursor: earliest free time today (respect "now" for today)
        cursor = max(day_start, start_search)
        if cursor.date() != current_day.date():
            # start_search is already past today, skip
            current_day += timedelta(days=1)
            continue

        # Today's busy events
        today_busy = [(bs, be, t) for bs, be, t in busy_intervals
                      if bs.date() == current_day.date()]

        # Walk through gaps between busy events
        for bs, be, _ in today_busy:
            # Available window: cursor → bs (minus buffer)
            available_end = bs - timedelta(minutes=BUFFER_MIN)
            duration_min  = (available_end - cursor).total_seconds() / 60

            if duration_min >= effort:
                slot_end = cursor + timedelta(minutes=effort)
                if slot_end <= available_end and slot_end <= deadline:
                    valid_slots.append({
                        "start": cursor.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                        "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                        "day":   cursor.strftime("%A %Y-%m-%d"),
                    })

            # Advance cursor past this busy event + buffer
            cursor = max(cursor, be + timedelta(minutes=BUFFER_MIN))

        # Final gap: cursor → day_end
        duration_min = (day_end - cursor).total_seconds() / 60
        if duration_min >= effort:
            slot_end = cursor + timedelta(minutes=effort)
            if slot_end <= day_end and slot_end <= deadline:
                valid_slots.append({
                    "start": cursor.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                    "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                    "day":   cursor.strftime("%A %Y-%m-%d"),
                })

        current_day += timedelta(days=1)

    return valid_slots


# ─────────────────────────────────────────────────────────────────────────────
# THE NODE
# ─────────────────────────────────────────────────────────────────────────────

def scheduler_node(state: dict) -> dict:
    signal = state["current_signal"]
    effort = get_effort(state)

    print(f"  [scheduler] Finding slot for: {signal['title']} | effort={effort} min")

    # Determine the search window
    now = datetime.now(CAIRO_OFFSET).replace(tzinfo=None)

    deadline = signal.get("deadline")
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline).replace(
                hour=WORK_END_HOUR, minute=0, second=0
            )
        except Exception:
            deadline_dt = now + timedelta(days=HORIZON_DAYS)
    else:
        deadline_dt = now + timedelta(days=HORIZON_DAYS)

    # Run the deterministic search
    busy_slots = get_busy_slots()
    available  = find_available_slots(busy_slots, effort, now, deadline_dt)

    print(f"  [scheduler] Found {len(available)} valid slot(s).")
    if available:
        for s in available[:5]:  # show first 5 for debugging
            print(f"    - {s['day']} | {s['start']} → {s['end']}")

    if not available:
        print(f"  [scheduler] ❌ No valid slot found. Escalating to HITL.")
        return {
            **state,
            "proposed_slot": None,
            "conflict_found": False,
            "conflicting_event": None,
        }

    # Pick earliest valid slot
    chosen = available[0]
    proposed = {
        "proposed_start": chosen["start"],
        "proposed_end":   chosen["end"],
        "reasoning":      f"Earliest valid slot: {chosen['day']}",
    }

    print(f"  [scheduler] ✅ Selected: {chosen['day']} | {chosen['start']} → {chosen['end']}")

    return {
        **state,
        "proposed_slot": proposed,
        "conflict_found": False,
        "conflicting_event": None,
    }
    
    