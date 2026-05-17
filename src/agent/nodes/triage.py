from datetime import datetime, timezone
import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from src.agent.prompts import TRIAGE_PROMPT
from src.database.db_utils import get_connection, log_audit_action_conn

llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)


def classify(signal: dict) -> dict:

    total_estimated_effort=signal.get("total_estimated_effort","No estimated effort")
    print("total_estimated_effort :", total_estimated_effort)
    deadline  = signal.get("deadline")
    issue_type = signal.get("issue_type")  
    today      = datetime.now(timezone.utc).strftime("%Y-%m-%d")  
    print("deadline for task : ",deadline)
    user_msg = f""" 
Today's date: {today}    
Source: {signal['source']}
Title: {signal['title']}
Description: {signal.get('description', 'None')}
Current start_time: {signal.get('start_time', 'Not scheduled')}
Current priority: {signal.get('priority', 5)}
End Time: {signal.get('end_time',"No given end time")}
Deadline: {deadline if deadline else 'None'}
Issue type: {issue_type if issue_type else 'N/A'}
Estimated Effort Minutes : {total_estimated_effort}
"""
    response = llm.invoke([
        SystemMessage(content=TRIAGE_PROMPT),
        HumanMessage(content=user_msg),
    ])
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


def apply_to_db(event_id: str, source: str, decision: dict):
    conn = get_connection()
    cursor = conn.cursor()

    if decision.get("dismiss"):
        cursor.execute(
            "UPDATE calendar_shadow SET status = 'Dismissed',is_triaged = 1, WHERE event_id = ?",
            (event_id,),
        )
        log_audit_action_conn(
            conn=conn,
            event_id=event_id,
            change_type="Deleted",
            reasoning_statement=f"Triage dismissed: {decision['reasoning']}",
        )
    else:
        cursor.execute("""
            UPDATE calendar_shadow
            SET flexibility_score = ?,
                priority          = ?,
                status            = CASE
                                          WHEN status = 'Scheduled' THEN 'Scheduled'
                                          ELSE 'Confirmed'
                                        END,
                is_triaged = 1,
                start_time        = COALESCE(?, start_time),
                end_time          = COALESCE(?, end_time)
            WHERE event_id = ?
        """, (
            decision["flexibility_score"],
            decision["priority"],
            decision.get("extracted_start"),
            decision.get("extracted_end"),
            event_id,
        ))

        if source == "Jira" and decision.get("estimated_effort_minutes"):
            effort = decision["estimated_effort_minutes"]
            cursor.execute("""
                UPDATE task_metadata
                SET total_estimated_effort = ?,
                    remaining_effort       = ?,
                    effort_needs_triage    = 0
                WHERE task_id = ?
            """, (effort, effort, event_id))

        log_audit_action_conn(
            conn=conn,
            event_id=event_id,
            change_type="Created",
            reasoning_statement=f"Triage: flex={decision['flexibility_score']} pri={decision['priority']}. {decision['reasoning']}",
        )

    conn.commit()
    conn.close()


def triage_node(state: dict) -> dict:
    signal = state["current_signal"]
    errors = state.get("errors", [])

    try:
        decision = classify(signal)
        apply_to_db(signal["event_id"], signal["source"], decision)
        
        print("-" * 50 +"  triage result "+ "-" * 50)

    

        print(f"  [triage] flex={decision['flexibility_score']} "
              f"pri={decision['priority']} dismiss={decision.get('dismiss', False)}"
              f"effort={decision['estimated_effort_minutes']} ")
        print(f"  [triage] reasoning: {decision['reasoning']}")
        
        print("-" * 100)

        return {**state, "triage_decision": decision}

    except Exception as e:
        err = f"Triage failed for {signal['event_id']}: {e}"
        errors.append(err)
        print(f" {err}")
        return {**state, "triage_decision": None, "errors": errors}