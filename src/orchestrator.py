from apscheduler.schedulers.asyncio import AsyncIOScheduler

import os
import asyncio
import json
from datetime import datetime, timezone, timedelta
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from dotenv import load_dotenv

from src.ingestion.poller import poll_jira, poll_gmail, strategic_calendar_sync
from src.agent.graph import agent_buddy_graph
from src.database.db_utils import get_connection


def get_pending_signals() -> list[dict]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                cs.event_id, cs.source, cs.title, cs.description,
                cs.start_time, cs.end_time, cs.priority, cs.flexibility_score,
                tm.deadline,        
                tm.issue_type,
                tm.total_estimated_effort      
            FROM calendar_shadow cs
            LEFT JOIN task_metadata tm ON tm.task_id = cs.event_id
            WHERE cs.is_triaged = 0
            ORDER BY cs.priority DESC
        """)
        rows = [dict(r) for r in cursor.fetchall()]
        return rows
    finally: 
        conn.close()   


def _empty_state(signal: dict) -> dict:
    return {
        "current_signal": signal,
        "triage_decision": None,
        "proposed_slot": None,
        "conflict_found": False,
        "conflicting_event": None,
        "retry_count": 0,
        "hitl_decision": None,
        "errors": [],
        "failed_slots": [], 
        "calendar_push": None
    }
async def push_to_google_calendar(push: dict) -> bool:
    """Async Calendar push — runs in the orchestrator's event loop."""
    google_env = os.environ.copy()
    google_env["GOOGLE_CLIENT_ID"]     = os.getenv("GOOGLE_CLIENT_ID", "")
    google_env["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_env["GOOGLE_REFRESH_TOKEN"] = os.getenv("GOOGLE_REFRESH_TOKEN", "")

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@gongrzhe/server-calendar-mcp"],
        env=google_env,
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool(
                    "create_event",
                    arguments={
                        "summary":     push["title"],
                        "description": push.get("description", ""),
                        "start":       {"dateTime": push["start"], "timeZone": "Africa/Cairo"},
                        "end":         {"dateTime": push["end"],   "timeZone": "Africa/Cairo"},
                    },
                )

                raw = response.content[0].text.strip()
                print(f"  [calendar] Event created: {raw[:100]}")
                return True

    except Exception as e:
        print(f"  calendar Push failed: {e}")
        return False
    


async def run_cycle():
    print(f"\nOrchestrator Cycle started at {datetime.now(timezone.utc).strftime('%H:%M:%S')}")

    # await poll_jira()
    # await poll_gmail()

    signals = get_pending_signals()
    print(f"Orchestrator {len(signals)} signal(s) to process.")


    for signal in signals:
        try:
            print(f"started graph on signal {signal}")
            final_state=agent_buddy_graph.invoke(_empty_state(signal))
            push = final_state.get("calendar_push")
            if push:
                print(f"  [calendar] Pushing to Google Calendar...")
                await push_to_google_calendar(push)
            
        except Exception as e:
            print(f"Failed on {signal['event_id']}: {e}")

    print("[Orchestrator] Cycle complete.")


async def start():
    
    await run_cycle()
    # scheduler = AsyncIOScheduler(timezone="UTC")

    # scheduler.add_job(run_cycle, "interval", minutes=15,
    #                   next_run_time=datetime.now(timezone.utc))
    # scheduler.add_job(strategic_calendar_sync, "interval", weeks=1,
    #                   next_run_time=datetime.now(timezone.utc) + timedelta(weeks=1))

    # print("=" * 50)
    # print("  Agent Buddy Orchestrator — Started")
    # print("=" * 50)
 

    # scheduler.start()
    # await asyncio.Event().wait()