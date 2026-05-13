import os
import asyncio
import json
from datetime import datetime, timezone
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from dotenv import load_dotenv
from src.database.db_utils import get_connection, log_audit_action_conn

load_dotenv(override=True)


async def push_to_google_calendar(
    title: str,
    description: str,
    start: str,
    end: str,
) -> bool:
    """
    Pushes a scheduled event to Google Calendar via MCP.
    Returns True on success, False on failure.
    """
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
                        "summary":     title,
                        "description": description or "",
                        "start":       {"dateTime": start, "timeZone": "Africa/Cairo"},
                        "end":         {"dateTime": end,   "timeZone": "Africa/Cairo"},
                    },
                )

                raw = response.content[0].text.strip()
                print(f"  [executor] Google Calendar response: {raw[:100]}")
                return True

    except Exception as e:
        print(f"  [executor] Google Calendar push failed: {e}")
        return False

def executor_node(state: dict) -> dict:
    signal   = state["current_signal"]
    proposed = state.get("proposed_slot")
    event_id = signal["event_id"]
    source   = signal["source"]

    conn = get_connection()
    try:
        cursor = conn.cursor()

        if proposed:
            # Jira task — planner found a slot
            start = proposed["proposed_start"]
            end   = proposed["proposed_end"]

            cursor.execute("""
                UPDATE calendar_shadow
                SET start_time = ?,
                    end_time   = ?,
                    status     = 'Scheduled'
                WHERE event_id = ?
            """, (start, end, event_id))

            log_audit_action_conn(
                conn, event_id, "Rescheduled",
                f"Planner scheduled '{signal['title']}' at {start} → {end}. "
                f"{proposed.get('reasoning', '')}",
            )
            conn.commit()
            print(f"  [executor]  SQLite updated: {signal['title']} → {start} to {end}")

            # Push to Google Calendar
            success = asyncio.run(
                push_to_google_calendar(
                    title=f"Deep Work: {signal['title']}",
                    description=signal.get("description", ""),
                    start=start,
                    end=end,
                )
            )
            if success:
                print(f"  [executor]  Google Calendar updated.")
            else:
                print(f"  [executor]  SQLite updated but Calendar push failed.")

        else:
            # Google Calendar / Gmail — already has a slot, just confirm it
            cursor.execute("""
                UPDATE calendar_shadow
                SET status = 'Scheduled'
                WHERE event_id = ?
            """, (event_id,))

            log_audit_action_conn(
                conn, event_id, "Created",
                f"Confirmed existing slot for '{signal['title']}' from {source}.",
            )
            conn.commit()
            print(f"  [executor] Confirmed existing slot: {signal['title']}")

    finally:
        conn.close()

    return state
