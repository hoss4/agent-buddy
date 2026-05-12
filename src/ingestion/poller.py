import asyncio
import os
import json
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


# ──────────────────────────────────────────────────────────────────────────────
# JIRA POLL
# ──────────────────────────────────────────────────────────────────────────────

async def poll_jira():
    """Checks for new/updated Jira tasks since the last poll."""
    since = _last_poll["jira"]
    _last_poll["jira"] = datetime.now(timezone.utc)

    print(f"\n Polling Jira for changes since {since.strftime('%H:%M:%S')}...")

    server_params = StdioServerParameters(
        command="python",
        args=[str(JIRA_SERVER_PATH)],
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                response = await session.call_tool(
                    "get_assigned_tasks",
                    arguments={},
                )

                # FastMCP returns one content item per task
                all_tasks = [json.loads(item.text) for item in response.content]

                # Filter to only tasks updated since last poll
                new_tasks = [
                    t for t in all_tasks
                    if t.get("task_id")  # basic sanity check
                ]

                if not new_tasks:
                    print(" Jira: no updates.")
                    return

                print(f" Jira: {len(new_tasks)} task(s) found.")

                for task in new_tasks:
                    try:
                        task_id = task["task_id"]
                        effort  = task["effort_minutes"]

                        upsert_task_metadata(
                            task_id=task_id,
                            title=task["title"],
                            description=task["description"],
                            issue_type=task["issue_type"],
                            total_estimated_effort=effort,
                            remaining_effort=effort,
                            deadline=task["deadline"],
                            numeric_priority=task["numeric_priority"],
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
                            priority=task["numeric_priority"],
                            flexibility_score=1,
                            status="Pending_Triage",
                        )

                        log_audit_action(
                            event_id=task_id,
                            change_type="Created",
                            reasoning_statement=f" detected new/updated Jira task: {task['title']}",
                        )
                        print(f" {task_id} — {task['title']} | priority={task['numeric_priority']} | deadline={task['deadline'] or 'none'}")

                    except Exception as e:
                        print(f" Error processing task {task.get('task_id', '?')}: {e}")

    except Exception as e:
        print(f" Jira poll failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# GMAIL POLL
# ──────────────────────────────────────────────────────────────────────────────

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

                
                tools = await session.list_tools()
                print("[Sentry] Gmail MCP tools:", [t.name for t in tools.tools])

                response = await session.call_tool(
                    "search_emails",
                    arguments={"query": query, "maxResults": 10},
                )

                # ── DEBUG: print raw response ─────────────────────────────
                print("[Sentry] Gmail raw response:")
                for i, item in enumerate(response.content):
                    print(f"  [{i}] type={type(item)} text={item.text[:300]}")
                
                response = await session.call_tool(
                    "search_emails",
                    arguments={"query": query, "maxResults": 10},
                )
                
                

                raw = response.content[0].text.strip()
                if not raw:
                    print(" Gmail: no new appointment emails.")
                    return

                try:
                    emails = json.loads(raw)
                except json.JSONDecodeError:
                    print(" Gmail: could not parse response.")
                    return

                if not emails:
                    print(" Gmail: no new appointment emails.")
                    return

                print(f" Gmail: {len(emails)} potential appointment email(s).")

                for email_data in emails:
                    try:
                        msg_id  = email_data.get("id")
                        subject = email_data.get("subject", "Untitled Email")
                        body    = email_data.get("body", "") or ""
                        date    = email_data.get("date")

                        if not msg_id or not date:
                            continue

                        normalized_date = normalize_datetime(date)

                        upsert_calendar_event(
                            event_id=f"gmail_{msg_id}",
                            source="Gmail",
                            title=f"[Email] {subject}",
                            description=body[:500],
                            start_time=normalized_date,
                            end_time=normalized_date,
                            original_start_time=normalized_date,
                            priority=5,
                            flexibility_score=0,   # assume Fixed until Triage refines
                            status="Pending_Triage",
                        )

                        log_audit_action(
                            event_id=f"gmail_{msg_id}",
                            change_type="Created",
                            reasoning_statement=f" flagged email as potential appointment: '{subject}'",
                        )
                        print(f"  ✓ Flagged: {subject}")

                    except Exception as e:
                        print(f"  ⚠ Error processing email: {e}")

    except Exception as e:
        print(f"Gmail poll failed: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULER
# ──────────────────────────────────────────────────────────────────────────────

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
        minutes=15,
        next_run_time=datetime.now(timezone.utc),
    )

    print("=" * 50)
    print("  Agent Buddy Sentry — Started")
    print("  Polling every 15 minutes")
    print("  First run: NOW")
    print("=" * 50)

    scheduler.start()

    try:
        await asyncio.Event().wait()  # run forever
    except (KeyboardInterrupt, SystemExit):
        print("\n[Sentry] Shutting down gracefully.")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())