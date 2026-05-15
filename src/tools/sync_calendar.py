# src/ingestion/bootstrap_calendar.py

import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import upsert_calendar_event, log_audit_action

load_dotenv(override=True)




def normalize_datetime(dt_string: str) -> str:
    """
    Ensures we always store a full datetime string in SQLite.
    Converts all-day date strings (e.g. '2025-07-15') to midnight timestamps.
    """
    if not dt_string:
        return None
    # Already a full datetime
    if "T" in dt_string:
        return dt_string
    # All-day event — convert to midnight
    try:
        dt = datetime.strptime(dt_string, "%Y-%m-%d")
        return dt.isoformat() + "T00:00:00"
    except ValueError:
        return dt_string


async def sync_google_calendar():
    """Fetches 90 days of events from Google Calendar and upserts them to SQLite."""
    print("--- Starting Google Calendar Bootstrap ---")

    google_env = os.environ.copy()
    google_env["GOOGLE_CLIENT_ID"] = os.getenv("GOOGLE_CLIENT_ID", "")
    google_env["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_env["GOOGLE_REFRESH_TOKEN"] = os.getenv("GOOGLE_REFRESH_TOKEN", "")

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@gongrzhe/server-calendar-mcp"],
        env=google_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to Google Calendar MCP.")

            now = datetime.now(timezone.utc)
            time_min = now.isoformat()
            time_max = (now + timedelta(days=90)).isoformat()

            print(f"Fetching {now.strftime('%Y-%m-%d')} → {(now + timedelta(days=90)).strftime('%Y-%m-%d')}")

            response = await session.call_tool(
                "list_events",
                arguments={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
            )

            # ── Safe JSON parsing ──────────────────────────────────────────
            raw = response.content[0].text.strip()
            print(raw)
            if not raw:
                print("MCP returned empty response.")
                return

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                # Try to find JSON substring as fallback
                for marker in ("[", "{"):
                    idx = raw.find(marker)
                    if idx != -1:
                        try:
                            parsed = json.loads(raw[idx:])
                            break
                        except json.JSONDecodeError:
                            continue
                else:
                    print(f"Could not parse MCP response as JSON:\n{raw[:300]}")
                    return

            events = (
                parsed.get("items", parsed)
                if isinstance(parsed, dict)
                else parsed
            )

            if not events:
                print("No events found in this timeframe.")
                return

            print(f"Found {len(events)} events. Saving to database...")

            saved, skipped = 0, 0
            for event in events:
                 
                try:
                    event_id = event.get("id")
                    title = event.get("summary", "Untitled Event")
                    description = event.get("description", "No Description provided") 
                    print(" event title : ",title)
                    if title.startswith("Deep Work:"):
                        print(f"  [sync] Skipping agent-created event: {title}")
                        continue
                    start_raw = (event.get("start") or {})
                    end_raw   = (event.get("end")   or {})
                    start_time = normalize_datetime(
                        start_raw.get("dateTime") or start_raw.get("date")
                    )
                    end_time = normalize_datetime(
                        end_raw.get("dateTime") or end_raw.get("date")
                    )

                    if not event_id or not start_time:
                        skipped += 1
                        continue


                    upsert_calendar_event(
                        event_id=f"gcal_{event_id}",
                        source="Google_Calendar",
                        title=title,
                        description=description,
                        start_time=start_time,
                        end_time=end_time,
                        original_start_time=start_time,
                        priority=5,
                        flexibility_score=1,
                        status="Scheduled",
                    )
                    saved += 1

                except Exception as e:
                    print(f"Skipped event due to error: {e}")
                    skipped += 1

       
            print(f"Calendar sync complete — {saved} saved, {skipped} skipped.")


if __name__ == "__main__":
    asyncio.run(sync_google_calendar())