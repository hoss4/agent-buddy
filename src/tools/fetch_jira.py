# src/ingestion/bootstrap_jira.py

import asyncio
import json
import sys
from pathlib import Path
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import upsert_task_metadata, upsert_calendar_event, log_audit_action

load_dotenv(override=True)


JIRA_SERVER_PATH = Path(__file__).parent/ "jira_custom_mcp.py"
print(JIRA_SERVER_PATH)

async def sync_jira_tasks():
    print("--- Starting Jira Task Bootstrap ---")

    server_params = StdioServerParameters(
        command="python",
        args=[str(JIRA_SERVER_PATH)],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to local Jira MCP server.")

            response = await session.call_tool(
                "get_assigned_tasks",
                arguments={},
            )
            
            tasks = [json.loads(item.text) for item in response.content]
            # print(response)
            # raw = response.content[0].text
            # print(raw)
            # print(type(raw))
            # tasks = json.loads(raw) if isinstance(raw, str) else raw
            # tasks = json.loads(response.content[0].text)⚠ S
            # print(tasks)
            # print(type(tasks))

                

            
            if not tasks:
                print("No open tasks found.")
                return

            print(f"Found {len(tasks)} task(s). Saving...\n")

            saved, skipped = 0, 0
            for task in tasks:
                try:
                    task_id = task["task_id"]
                    effort  = task["effort_minutes"]  # 0 = Triage Agent will estimate

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

                    print(f"  {task_id} — {task['title']}")
                    print(f"  type={task['issue_type']} | priority={task['numeric_priority']} | effort={effort or 'TBD'}min | deadline={task['deadline'] or 'none'}")
                    saved += 1

                except Exception as e:
                    print(f"skipped {task.get('task_id', '?')}: {e}")
                    skipped += 1

           
            print(f"Jira sync complete — {saved} saved, {skipped} skipped.")


if __name__ == "__main__":
    asyncio.run(sync_jira_tasks())