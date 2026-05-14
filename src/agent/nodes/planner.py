import json
from datetime import datetime, timedelta, timezone
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.state import AgentState
from src.agent.prompts import PLANNER_PROMPT
from src.database.db_utils import get_connection

MAX_RETRIES = 3
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
CAIRO_OFFSET = timezone(timedelta(hours=2))

def get_busy_slots(days_ahead: int = 14) -> list[dict]:
    """
    Returns all scheduled/confirmed events in the next N days.
    This is what the Planner sees as "occupied time".
    """
    now     = datetime.now(timezone.utc)
    horizon = (now + timedelta(days=days_ahead)).isoformat()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT title, start_time, end_time, flexibility_score
            FROM calendar_shadow
            WHERE start_time >= ?
              AND start_time <= ?
              AND status NOT IN ('Dismissed', 'Pending_Triage', 'Completed')
            ORDER BY start_time ASC
        """, (now.isoformat(), horizon))
        return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def format_busy_slots(busy: list[dict]) -> str:
    if not busy:
        return "No events scheduled in the next 14 days."
    lines = []
    for slot in busy:
        try:
            dt = datetime.fromisoformat(slot["start_time"])
            day_label = dt.strftime("%A")  # "Monday", "Tuesday", etc
        except:
            day_label = "?"
        flex = "Flexible" if slot["flexibility_score"] == 1 else "Fixed"
        lines.append(f"  - {day_label} {slot['start_time']} → {slot['end_time']}: "
            f"{slot['title']} [{flex}]")
    return "\n".join(lines)


def get_effort(state: dict) -> int:
    # 1. From triage decision (LLM estimated)
    triage = state.get("triage_decision") or {}
    if triage.get("estimated_effort_minutes"):
        return triage["estimated_effort_minutes"]

    # 2. From signal (already fetched from task_metadata via JOIN)
    effort = state["current_signal"].get("total_estimated_effort")
    if effort:
        return effort

    return 60  

def planner_node(state: AgentState) -> AgentState:
    signal = state["current_signal"]
    retry_count = state.get("retry_count", 0)
    failed_slots = state.get("failed_slots", [])
    errors = state.get("errors", [])

    # Guard: max retries exceeded
    if retry_count >= MAX_RETRIES:
        print(f"  [planner] Max retries ({MAX_RETRIES}) reached. Escalating to HITL.")
        return {**state, "proposed_slot": None}

    print(f"  [planner] Finding slot for: {signal['title']} "
          f"(attempt {retry_count + 1}/{MAX_RETRIES})")

    busy_slots   = get_busy_slots()
    
    print("---------------busy slots ---------------------")
    print(format_busy_slots(busy_slots))
    print("---------------busy slots ---------------------")
    
    
    now_cairo    = datetime.now(CAIRO_OFFSET)
    now_str      = now_cairo.strftime("%Y-%m-%dT%H:%M:%S+02:00")
    today_str    = now_cairo.strftime("%Y-%m-%d %H:%M")
    day_name   = now_cairo.strftime("%A")
    print("day_name : ",day_name)

    effort = get_effort(state)
    failed_slots_str = (
        "\n".join([f"  - {s['start']} → {s['end']} (reason: {s['reason']})"
                   for s in failed_slots])
        if failed_slots else "None"
    )

    user_msg = f"""

TODAY: {day_name.upper()}, {today_str} (Cairo, UTC+2)
DO NOT propose any slot starting before: {now_str}

Task title: {signal['title']}
Description: {signal.get('description', 'None')}
Estimated effort: {effort} minutes (slot MUST be exactly this duration)
Priority: {signal.get('priority', 5)}/10
Deadline: {signal.get('deadline', 'None')}

Current busy schedule (next 14 days — note the day names):
{format_busy_slots(busy_slots)}

Previously failed slots (do NOT propose these):
{failed_slots_str}

Find a slot for exactly {effort} minutes. Remember:
- Weekends are Saturday and Sunday (skip them)
- Priority {signal.get('priority', 5)} means schedule within 3-4 days if possible
- Today is {day_name} — count forward from today
- Leave at least 15 minutes buffer between events.
Find exactly {effort} minutes. If the task doesn't fit today (remaining hours < {effort} min), 
schedule it later. NEVER schedule outside 9 AM to 7 PM.
"""



    try:
        response = llm.invoke([
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=user_msg),
        ])
        raw = response.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        proposed = json.loads(raw.strip())
        
        if not proposed.get("proposed_start") or not proposed.get("proposed_end"):
            print(f"  [planner] Could not find a valid slot.")
            print(f"  [planner] Reasoning: {proposed.get('reasoning', 'No reasoning provided')}")
            return {
                **state,
                "proposed_slot": None,  
                "retry_count":   retry_count + 1,
            }
        
        
        print(f"  [planner] Proposed: {proposed['proposed_start']} → {proposed['proposed_end']}")
        print(f"  [planner] Reasoning: {proposed['reasoning']}")

        return {
            **state,
            "proposed_slot": proposed,
            "retry_count":   retry_count + 1,
        }

    except Exception as e:
        err = f"Planner failed for {signal['event_id']}: {e}"
        errors.append(err)
        print(f"{err}")
        return {**state, "proposed_slot": None, "errors": errors}