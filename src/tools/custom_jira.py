import os
import httpx
from dotenv import load_dotenv

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))
from src.database.db_utils import upsert_task_metadata

load_dotenv(override=True)


def sync_jira_tasks():
    """Fetches open tasks assigned to you from Jira and upserts them to SQLite."""
    print("--- Starting Jira Task Sync ---")

    email = os.getenv("JIRA_EMAIL", "")
    api_token = os.getenv("JIRA_API_TOKEN", "")
    base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")

    if not all([email, api_token, base_url]):
        print(" Missing JIRA_EMAIL, JIRA_API_TOKEN, or JIRA_BASE_URL in environment.")
        return

    url = f"{base_url}/rest/api/3/search/jql"
    payload = {
        "jql": f'assignee = "{email}" ORDER BY updated DESC',
        "maxResults": 50,
        "fields": ["summary", "description", "status", "priority", "assignee","duedate","timeoriginalestimate","issuetype"],
    }

    try:
        response = httpx.post(url, json=payload, auth=(email, api_token), timeout=15)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f" Jira API error {e.response.status_code}: {e.response.text}")
        return
    except httpx.RequestError as e:
        print(f" Request failed: {e}")
        return

    issues = response.json().get("issues", [])

    if not issues:
        print("No open issues found assigned to you.")
        return

    print(f"Found {len(issues)} issue(s). Saving to database...")
    print(issues[0])
    for issue in issues:
        
        task_id = issue.get("key")
        fields = issue.get("fields", {})
        duedate=fields.get("duedate")
        timeestimate=fields.get("timeoriginalestimate")

        title = fields.get("summary") or "Untitled Task"

        description_raw = fields.get("description")
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

        status = (fields.get("status") or {}).get("name", "Open")
        print(task_id)
        print(title)
        print(description)
        print(timeestimate)
        print(duedate)
        print(status)

        upsert_task_metadata(
            task_id=task_id,
            title=title,
            description=description[:500],
            total_estimated_effort=timeestimate,
            remaining_effort=0,
            deadline=duedate,
            dependency_id=None,
            status=status
        )
        print(f"  Saved: {task_id} — {title}")

    print("Jira sync complete!")


if __name__ == "__main__":
    sync_jira_tasks()
