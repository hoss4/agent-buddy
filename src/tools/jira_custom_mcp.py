import os
import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv(override=True)

mcp = FastMCP("jira-server")

JIRA_EMAIL     = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_BASE_URL  = os.getenv("JIRA_BASE_URL", "").rstrip("/")


def _extract_plain_text(description_raw) -> str:
    if not description_raw:
        return "No description provided."
    if isinstance(description_raw, str):
        return description_raw[:500]
    if isinstance(description_raw, dict):
        texts = []
        for block in description_raw.get("content", []):
            for node in block.get("content", []):
                if node.get("type") == "text":
                    texts.append(node.get("text", ""))
        return (" ".join(texts) or "No description provided.")[:500]
    return "No description provided."


def _map_priority(priority_name: str) -> int:
    return {
        "highest": 9, "high": 7,
        "medium":  5,
        "low":     3, "lowest": 1,
    }.get(priority_name.lower(), 5)


@mcp.tool()
def get_assigned_tasks() -> list[dict]:
    """
    Fetches all active Jira tasks assigned to the configured user.
    Returns: task_id, title, description, status, numeric_priority,
             jira_priority, effort_minutes, deadline, issue_type.
    """
    url = f"{JIRA_BASE_URL}/rest/api/3/search/jql"
    payload = {
        "jql": (
            f'assignee = "{JIRA_EMAIL}" '
            f'AND status NOT IN ("Done","Closed","Resolved","Cancelled") '
            f'ORDER BY updated DESC'
        ),
        "maxResults": 50,
        "fields": [
            "summary", "description", "status",
            "priority", "duedate", "timeoriginalestimate", "issuetype",
        ],
    }

    with httpx.Client(timeout=15) as client:
        response = client.post(
            url,
            json=payload,
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()

    results = []
    for issue in response.json().get("issues", []):
        fields = issue.get("fields", {})

        raw_estimate   = fields.get("timeoriginalestimate")
        effort_minutes = (raw_estimate // 60) if raw_estimate else 0

        priority_name = (fields.get("priority") or {}).get("name", "medium")
        
        print("here-------------------")
        print(f"retrieved task: {issue.get("key")} {fields.get("summary")} {effort_minutes} {raw_estimate}")

        results.append({
            "task_id":          issue.get("key"),
            "title":            fields.get("summary") or "Untitled Task",
            "description":      _extract_plain_text(fields.get("description")),
            "status":           (fields.get("status") or {}).get("name", "Open"),
            "numeric_priority": _map_priority(priority_name),
            "jira_priority":    priority_name,
            "effort_minutes":   effort_minutes,  # 0 = Triage Agent will estimate
            "deadline":         fields.get("duedate"),
            "issue_type":       (fields.get("issuetype") or {}).get("name", "Task"),
        })

    return results


if __name__ == "__main__":
    mcp.run(transport="stdio")