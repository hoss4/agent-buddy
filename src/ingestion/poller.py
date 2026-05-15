import asyncio
import os
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

 
import sys
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import (
    upsert_task_metadata,
    upsert_calendar_event,
    log_audit_action,
    get_task_core_fields,
    update_task_critical_fields,
    event_exists
)
from src.tools.sync_calendar import normalize_datetime

load_dotenv(override=True)

print
JIRA_SERVER_PATH = Path(__file__).parent.parent / "tools" / "jira_custom_mcp.py"
print(JIRA_SERVER_PATH)

_last_poll: dict[str, datetime] = {
    "jira":  datetime.now(timezone.utc) - timedelta(minutes=15),
    "gmail": datetime.now(timezone.utc) - timedelta(minutes=15),
}


def parse_gmail_response(raw: str) -> list[dict]:
    """
    Parses the plain text Gmail MCP response into a list of email dicts.
    Format per email:
        ID: <id>
        Subject: <subject>
        From: <sender>
        Date: <date>
        (optional body)
    """
    emails = []
    # Split on ID: to separate individual emails
    blocks = re.split(r'(?=^ID: )', raw, flags=re.MULTILINE)

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        email = {}

        id_match      = re.search(r'^ID:\s*(.+)$',      block, re.MULTILINE)
        subject_match = re.search(r'^Subject:\s*(.+)$', block, re.MULTILINE)
        from_match    = re.search(r'^From:\s*(.+)$',    block, re.MULTILINE)
        date_match    = re.search(r'^Date:\s*(.+)$',    block, re.MULTILINE)

        if not id_match:
            continue

        email["id"]      = id_match.group(1).strip()
        email["subject"] = subject_match.group(1).strip() if subject_match else "No Subject"
        email["from"]    = from_match.group(1).strip()    if from_match    else ""
        email["date"]    = date_match.group(1).strip()    if date_match    else ""

        # Everything after the headers is the body
        header_end = max(
            (m.end() for m in [id_match, subject_match, from_match, date_match] if m),
            default=0,
        )
        body = block[header_end:].strip()
        email["body"] = body if body else ""

        emails.append(email)

    return emails


# JIRA POLL
async def poll_jira():
    since = _last_poll["jira"]
    _last_poll["jira"] = datetime.now(timezone.utc)

    print(f"\n[Sentry] Polling Jira since {since.strftime('%H:%M:%S')}...")

    server_params = StdioServerParameters(
        command="python",
        args=[str(JIRA_SERVER_PATH)],
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool("get_assigned_tasks", arguments={})
                all_tasks = [json.loads(item.text) for item in response.content]

                if not all_tasks:
                    print("Jira: no tasks found.")
                    return

                new_count, updated_count, skipped_count = 0, 0, 0

                for task in all_tasks:
                    try:
                        task_id          = task["task_id"]
                        new_priority     = task["numeric_priority"]
                        new_deadline     = task["deadline"]
                        existing         = get_task_core_fields(task_id)

                        # case 1 : new task 
                        if existing is None:
                            upsert_task_metadata(
                                task_id=task_id,
                                title=task["title"],
                                description=task["description"],
                                issue_type=task["issue_type"],
                                total_estimated_effort=task["effort_minutes"],
                                remaining_effort=task["effort_minutes"],
                                deadline=new_deadline,
                                numeric_priority=new_priority,
                                status=task["status"],
                            )
                            upsert_calendar_event(
                                event_id=task_id,
                                source="Jira",
                                title=task["title"],
                                description=task["description"],
                                start_time=None,
                                end_time=None,
                                original_start_time=None,
                                priority=new_priority,
                                flexibility_score=1,
                                status="Pending_Triage",
                            )
                            log_audit_action(
                                event_id=task_id,
                                change_type="Created",
                                reasoning_statement=f"New Jira task detected: {task['title']}",
                            )
                            print(f"new: {task_id} — {task['title']}")
                            new_count += 1

                        # case 2 : task exists
                        else:
                            priority_changed = existing["numeric_priority"] != new_priority
                            deadline_changed = existing["deadline"] != new_deadline

                            if priority_changed or deadline_changed:
                                update_task_critical_fields(
                                    task_id=task_id,
                                    numeric_priority=new_priority,
                                    deadline=new_deadline,
                                )
                                changes = []
                                if priority_changed:
                                    changes.append(f"priority {existing['numeric_priority']}→{new_priority}")
                                if deadline_changed:
                                    changes.append(f"deadline {existing['deadline']}→{new_deadline}")

                                log_audit_action(
                                    event_id=task_id,
                                    change_type="Rescheduled",
                                    reasoning_statement=f"Jira change detected on {task_id}: {', '.join(changes)}. Reset to Pending_Triage.",
                                )
                                print(f"  ↺ UPDATED: {task_id} — {', '.join(changes)}")
                                updated_count += 1

                            # case 3: Nothing changed skip entirely 
                            else:
                                skipped_count += 1

                    except Exception as e:
                        print(f"Error processing {task.get('task_id', '?')}: {e}")

                print(f"Jira done — {new_count} new, {updated_count} updated, {skipped_count} unchanged.")

    except Exception as e:
        print(f"Jira poll failed: {e}")
        
# GMAIL POLL

async def poll_gmail():
    """Checks Gmail for new appointment/logistics emails since last poll."""
    since = _last_poll["gmail"]
    _last_poll["gmail"] = datetime.now(timezone.utc)

    print(f"Polling Gmail for changes since {since.strftime('%H:%M:%S')}...")

    google_env = os.environ.copy()
    google_env["GOOGLE_CLIENT_ID"]     = os.getenv("GOOGLE_CLIENT_ID", "")
    google_env["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_env["GOOGLE_REFRESH_TOKEN"] = os.getenv("GOOGLE_REFRESH_TOKEN", "")

    after_ts = int(since.timestamp())
    query    = f"is:unread after:{after_ts} (appointment OR confirmation OR schedule OR meeting OR booking)"

    server_params = StdioServerParameters(
        command="npx",
        args=["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                today = datetime.now(timezone.utc).strftime("%Y/%m/%d")
                query = f"after:{today}"

          

                response = await session.call_tool(
                    "search_emails",
                    arguments={"query": query, "maxResults": 50},
                )
                       

                raw = response.content[0].text.strip()
                if not raw:
                    print("Gmail: no emails found.")
                    return

                emails = parse_gmail_response(raw)

                if not emails:
                    print("Gmail: no emails today.")
                    return

                print(f"Gmail: {len(emails)} email(s) found. Checking for new ones...")
                
                new_count, skipped_count = 0, 0
                for email_data in emails:
                    try:
                        msg_id  = email_data.get("id")
                        subject = email_data.get("subject", "Untitled Email")
                        body = email_data.get("body", "") 
                        date = email_data.get("date")
                        sender = email_data.get("from")   

                        if not msg_id or not date:
                            continue
                        
                        event_id = f"gmail_{msg_id}"
                        print(event_id)
                        existing = event_exists(event_id)  
                        print(existing)
                        
                        if existing is not None:
                            skipped_count += 1
                            continue

                        normalized_date = normalize_datetime(date)

                        upsert_calendar_event(
                            event_id=event_id,
                            source="Gmail",
                            title=subject,
                            description=f"From: {sender}\n\n{body[:400]}",
                            start_time=normalized_date,
                            end_time=normalized_date,
                            original_start_time=normalized_date,
                            priority=5,
                            flexibility_score=0,   
                            status="Pending_Triage",
                        )

                        log_audit_action(
                            event_id=f"gmail_{msg_id}",
                            change_type="Created",
                            reasoning_statement=f" flagged email as potential appointment: '{subject}'",
                        )
                        print(f"new email: {subject[:60]} | from: {sender}")
                        new_count += 1


                    except Exception as e:
                        print(f"Error processing email: {e}")
                
                print(f"Gmail poll done — {new_count} new, {skipped_count} already in DB.")

    except Exception as e:
        print(f"Gmail poll failed: {e}")

# calendar resync
async def strategic_calendar_sync():
    """
    Runs once a week.
    Re-fetches 3 months of Google Calendar events from today to:
    1. Slide the window forward (picks up the new week at the far end)
    2. Catch any events manually added since the last bootstrap
    """
    print("Starting weekly calendar refresh")

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

                now      = datetime.now(timezone.utc)
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

                raw = response.content[0].text.strip()
                if not raw:
                    print("No events returned.")
                    return

                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    for marker in ("[", "{"):
                        idx = raw.find(marker)
                        if idx != -1:
                            try:
                                parsed = json.loads(raw[idx:])
                                break
                            except json.JSONDecodeError:
                                continue
                    else:
                        print(f"Could not parse response.")
                        return

                events = (
                    parsed.get("items", parsed)
                    if isinstance(parsed, dict)
                    else parsed
                )

                if not events:
                    print("No events found in window.")
                    return

                new_count, skipped_count = 0, 0
                for event in events:
                    try:
                        event_id    = event.get("id")
                        title       = event.get("summary", "Untitled Event")
                        description = event.get("description", "") or ""

                        print(" event title : ",title)
                        if title.startswith("Deep Work:"):
                            print(f"  [sync] Skipping agent-created event: {title}")
                            continue

                        start_raw  = event.get("start") or {}
                        end_raw    = event.get("end")   or {}
                        start_time = normalize_datetime(
                            start_raw.get("dateTime") or start_raw.get("date")
                        )
                        end_time = normalize_datetime(
                            end_raw.get("dateTime") or end_raw.get("date")
                        )

                        if not event_id or not start_time:
                            continue

                        gcal_id = f"gcal_{event_id}"

                        # Only insert genuinely new events — never overwrite
                        # agent-managed fields on existing ones
                        if event_exists(gcal_id):
                            skipped_count += 1
                            continue

                        upsert_calendar_event(
                            event_id=gcal_id,
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

                        log_audit_action(
                            event_id=gcal_id,
                            change_type="Created",
                            reasoning_statement=f" new calendar event detected: '{title}'",
                        )
                        new_count += 1

                    except Exception as e:
                        print(f"Skipped event: {e}")

                print(f"Done — {new_count} new events, {skipped_count} already in DB.")
                log_audit_action(
                    event_id="STRATEGIC_SYNC",
                    change_type="Created",
                    reasoning_statement=f"Weekly strategic sync complete: {new_count} new, {skipped_count} existing.",
                )

    except Exception as e:
        print(f"[Strategic Sync] Failed: {e}")


# SCHEDULER

async def poll_all():
    """Runs both polls concurrently."""
    await asyncio.gather(
        poll_jira(),
        poll_gmail(),
    )

async def main():
    scheduler = AsyncIOScheduler(timezone="UTC")

    scheduler.add_job(
        poll_all,
        "interval",
        minutes=5,
        next_run_time=datetime.now(timezone.utc),
    )
    
    scheduler.add_job(
        strategic_calendar_sync,
        "interval",
        minutes=5,
       # weeks=1,
        next_run_time=datetime.now(timezone.utc) +timedelta(minutes=5),
        #+ timedelta(weeks=1),  
    )

    print("=" * 50)
    print("  Agent Buddy Sentry — Started")
    print("  Polling every 15 minutes")
    print("  First run: NOW")
    print("=" * 50)

    scheduler.start()

    try:
        await asyncio.Event().wait()  
    except (KeyboardInterrupt, SystemExit):
        print("poller shutting down gracefully.")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())