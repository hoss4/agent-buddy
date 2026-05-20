from datetime import datetime, timedelta, timezone
from src.database.db_utils import get_connection


CAIRO_OFFSET = timezone(timedelta(hours=2))
WORK_START_HOUR = 9
WORK_END_HOUR   = 18
BUFFER_MIN      = 15
HORIZON_DAYS    = 14



def to_naive(dt_str: str) -> datetime:

    if not dt_str:
        return None
    try:
        naive = dt_str.split("+")[0].split("Z")[0]
        return datetime.fromisoformat(naive)
    except Exception:
        return None


def get_cairo_now() -> datetime:
        
    return datetime.now().replace(microsecond=0)


def round_up_to_quarter(dt: datetime) -> datetime:
    
    minutes_to_add = (15 - dt.minute % 15) % 15
    if minutes_to_add == 0 and (dt.second > 0 or dt.microsecond > 0):
        minutes_to_add = 15
    rounded = dt + timedelta(minutes=minutes_to_add)
    return rounded.replace(second=0, microsecond=0)


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


def get_busy_slots(deadline: datetime) -> list[dict]:
   
    now = get_cairo_now()
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

    in_window = []
    for row in rows:
        busy_slot_start = to_naive(row["start_time"])
        busy_slot_end = to_naive(row["end_time"])
        if not busy_slot_start or not busy_slot_end:
            continue
        if busy_slot_end > now and busy_slot_start <= deadline:
            in_window.append(row)
    return in_window



def find_available_slots(busy: list[dict], effort: int, start_search: datetime, deadline: datetime) -> list[dict]:

    valid_slots = []
    busy_intervals = []
    
    for slot in busy:
        busy_slot_start = to_naive(slot["start_time"])
        busy_slot_end = to_naive(slot["end_time"])
        if busy_slot_start and busy_slot_end:
            busy_intervals.append((busy_slot_start, busy_slot_end, slot["title"]))
    busy_intervals.sort()

    # current day == start date
    current_day = start_search.replace(hour=0, minute=0, second=0, microsecond=0)
    # deadline
    end_day = deadline.replace(hour=0, minute=0, second=0, microsecond=0)

    # loop from start day (today) to end date (deadline)
    while current_day <= end_day:
        # skip if its a weekend (5,6)
        if current_day.weekday() >= 5:
            current_day += timedelta(days=1)
            continue

        # normalize current day period to be from 9 to 6
        day_start = current_day.replace(hour=WORK_START_HOUR, minute=0)
        day_end   = current_day.replace(hour=WORK_END_HOUR,   minute=0)

        # choose max 
        cursor = max(day_start, start_search)
        # Round up to next quarter-hour for cleaner slots
        cursor = round_up_to_quarter(cursor)

        # get todays busy
        today_busy = []
        for busy_slot_start, busy_slot_end, t in busy_intervals:
            if busy_slot_start.date() == current_day.date():
                today_busy.append((busy_slot_start, busy_slot_end, t))

    
        for busy_slot_start, busy_slot_end, _ in today_busy:
            available_end = busy_slot_start - timedelta(minutes=BUFFER_MIN)
            duration_min  = (available_end - cursor).total_seconds() / 60

            if duration_min >= effort:
                slot_end = cursor + timedelta(minutes=effort)
                if slot_end <= available_end and slot_end <= deadline:
                    valid_slots.append({
                        "start": cursor.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                        "end":   slot_end.strftime("%Y-%m-%dT%H:%M:%S+02:00"),
                        "day":   cursor.strftime("%A %Y-%m-%d"),
                    })

            cursor = max(cursor, busy_slot_end + timedelta(minutes=BUFFER_MIN))
            cursor = round_up_to_quarter(cursor)

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



def scheduler_node(state: dict) -> dict:
    signal = state["current_signal"]
    effort = get_effort(state)
    print("task effort : ", effort)

    print(f"  [scheduler] Finding slot for: {signal['title']} | effort={effort} min")

    now = get_cairo_now()
    #print("date time now : ", now)

    deadline = signal.get("deadline")
    #print("deadline : ", deadline)
    if deadline:
        try:
            deadline_dt = datetime.fromisoformat(deadline).replace(hour=WORK_END_HOUR, minute=0, second=0)
        except Exception:
            deadline_dt = (now + timedelta(days=HORIZON_DAYS)).replace(hour=WORK_END_HOUR, minute=0, second=0)
    else:
        deadline_dt = (now + timedelta(days=HORIZON_DAYS)).replace(hour=WORK_END_HOUR, minute=0, second=0)
        
    print("deadline_dt : ",deadline_dt)
    busy_slots = get_busy_slots(deadline_dt)
    
    ## print busy slots :
    print("------------------busy slots--------------------")
    for slot in busy_slots:
        print(slot)
    print("------------------busy slots--------------------")
    
    available  = find_available_slots(busy_slots, effort, now, deadline_dt)
    print("available : ", available)

    print(f"  [scheduler] Found {len(available)} valid slot(s).")
    if available:
        for s in available[:5]:
            print(f"    - {s['day']} | {s['start']} → {s['end']}")

    if not available:
        print(f"  [scheduler] No valid slot found. Escalating to HITL.")
        return { **state, "proposed_slot": None}

    chosen = available[0]
    
    proposed = {
        "proposed_start": chosen["start"],
        "proposed_end":   chosen["end"],
        "reasoning":      f"Earliest valid slot: {chosen['day']}",
    }

    print(f"  [scheduler] Selected: {chosen['day']} | {chosen['start']} → {chosen['end']}")

    return { **state, "proposed_slot": proposed }