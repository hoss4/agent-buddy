import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

# Import our database utility
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import upsert_calendar_event

load_dotenv(override=True)

async def sync_google_calendar():
    """Fetches 90 days of events from Google Calendar and upserts them to SQLite."""
    print("--- Starting Google Calendar Sync ---")
    
    google_env = os.environ.copy()
    google_env["GOOGLE_CLIENT_ID"] = os.getenv("GOOGLE_CLIENT_ID", "")
    google_env["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_env["GOOGLE_REFRESH_TOKEN"] = os.getenv("GOOGLE_REFRESH_TOKEN", "placeholder")

    server_params = StdioServerParameters(
        command="npx", 
        args=["-y", "@gongrzhe/server-calendar-mcp"], 
        env=google_env
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("connected to calendar mcp.")

            # Calculate period (min,max)
            now = datetime.now(timezone.utc)
            time_min = now.isoformat()
            time_max = (now + timedelta(days=90)).isoformat()
            
            print(f" Fetching events from {now.strftime('%Y-%m-%d')} to {(now + timedelta(days=90)).strftime('%Y-%m-%d')}")

            try:
                response = await session.call_tool(
                    "list_events", 
                    arguments={
                        "timeMin": time_min,
                        "timeMax": time_max,
                        "singleEvents": "true",
                        "orderBy": "startTime"
                    }
                )

                raw_data = response.content[0].text
  
                # print("\n--- RAW RESPONSE FROM MCP ---")
                print(raw_data)

                raw_data = response.content[0].text.strip()
                
                if not raw_data:
                    print("server returned an empty string.")
                    return

                start_idx = raw_data.find('[')
                if start_idx == -1:
                    start_idx = raw_data.find('{')
                
                if start_idx != -1:
                    json_string = raw_data[start_idx:]
                else:
                    json_string = raw_data 

  
                parsed_data = json.loads(json_string)
                
                events = parsed_data.get("items", parsed_data) if isinstance(parsed_data, dict) else parsed_data
                if not events:
                    print("No events found in this timeframe.")
                    return

                print(f"Found {len(events)} events. Saving to database")

                for event in events:
                    event_id = event.get('id')
                    title = event.get('summary', 'Untitled Event')
                    description = event.get('description', '')
                    
                    start_data = event.get('start', {})
                    end_data = event.get('end', {})
                    start_time = start_data.get('dateTime') or start_data.get('date')
                    end_time = end_data.get('dateTime') or end_data.get('date')

                    if not event_id or not start_time:
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
                        flexibility_score=0, 
                        status='Confirmed'
                    )

                print("✅ Sync complete! items added to database.")

            except Exception as e:
                print(f"Error during sync: {e}")
if __name__ == "__main__":
    asyncio.run(sync_google_calendar())