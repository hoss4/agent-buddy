import asyncio
import os
import json
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import upsert_task_metadata

load_dotenv(override=True)


async def sync_jira_tasks():
    """Fetches open tasks assigned to you from Jira and upserts them to SQLite."""
    print("--- Starting Jira Task Sync ---")

    email = os.getenv("JIRA_EMAIL", "")
    api_token = os.getenv("JIRA_API_TOKEN", "")
    base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")

    if not all([email, api_token, base_url]):
        print("Missing JIRA_EMAIL, JIRA_API_TOKEN, or JIRA_BASE_URL in environment.")
        return

    jira_env = os.environ.copy()
    jira_env["JIRA_URL"] = base_url
    jira_env["JIRA_USERNAME"] = email
    jira_env["JIRA_API_TOKEN"] = api_token

    server_params = StdioServerParameters(
        command="uvx",
        args=["mcp-atlassian"],
        env=jira_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("Connected to Jira MCP.")
            print(email)

            jql = f'assignee = "{email}" ORDER BY updated DESC'

            response = await session.call_tool(
                "jira_search",
                arguments={"jql": jql, "limit": 50},
            )

            raw = response.content[0].text.strip()
            
            print(raw)

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                print("could not parse mcp response as json. raw output:")
                print(raw[:500])
                return

            issues = data.get("issues", data) if isinstance(data, dict) else data

            if not issues:
                print("No open issues found assigned to you.")
                return

            print(f"Found {len(issues)} issue(s). Saving to database...")

            for issue in issues:
                task_id = issue.get("key")
                

                title = issue.get("summary")

                description_raw = issue.get("description")
                if isinstance(description_raw, dict):
                    # Atlassian Document Format — extract plain text
                    texts = []
                    for block in description_raw.get("content", []):
                        for node in block.get("content", []):
                            if node.get("type") == "text":
                                texts.append(node.get("text", ""))
                    description = " ".join(texts) or "No description provided."
                else:
                    description = str(description_raw) if description_raw else "No description provided."

                fields = issue.get("status")
                status = issue.get("status").get("name")

                upsert_task_metadata(
                    task_id=task_id,
                    title=title,
                    description=description[:500],
                    total_estimated_effort=0,
                    remaining_effort=0,
                    deadline=None,
                    dependency_id=None,
                    status=status,
                )
                print(f"  Saved: {task_id} — {title}")

            print("✅ Jira sync complete!")


if __name__ == "__main__":
    asyncio.run(sync_jira_tasks())
