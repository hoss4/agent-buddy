from datetime import datetime, timedelta, timezone
from src.database.db_utils import get_connection, log_audit_action_conn



CAIRO_OFFSET = timezone(timedelta(hours=2))


now  = datetime.now().replace(microsecond=0)
print(now)

failed_priority=7



def best_match(candidates: list[dict], predicate) -> dict | None:
    filtered = [c for c in candidates if predicate(c)]
    if not filtered:
        return None
    print(filtered)
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]

def best_match_lower_flex(candidates: list[dict], failed_priority:int ) -> dict | None:
    filtered=[]
    for candidate in candidates:
        if candidate["flexibility_score"]==1 and candidate["priority"] < failed_priority :
            filtered.append(candidate) 
    if not filtered:
        return None
    print(filtered)
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]

def best_match_lower_flex(candidates: list[dict], failed_priority: int) -> dict | None:
    filtered = []
    for candidate in candidates:
        if candidate["flexibility_score"] == 1 and candidate["priority"] < failed_priority:
            filtered.append(candidate)
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


def best_match_higher_flex(candidates: list[dict], failed_priority: int) -> dict | None:
    filtered = []
    for candidate in candidates:
        if candidate["flexibility_score"] == 1 and candidate["priority"] >= failed_priority:
            filtered.append(candidate)
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]


def best_match_lower_fixed(candidates: list[dict], failed_priority: int) -> dict | None:
    filtered = []
    for candidate in candidates:
        if candidate["flexibility_score"] == 0 and candidate["priority"] < failed_priority:
            filtered.append(candidate)
    if not filtered:
        return None
    filtered.sort(key=lambda c: (c["priority"], c["start_time"]))
    return filtered[0]



failed_task ={
    "event_id":               "SCRUM-14",
    "source":                 "Jira",
    "title":                  "finish agent",
    "description":            "Implement the resolver node and HITL options",
    "start_time":             None,
    "end_time":               None,
    "priority":               5,
    "flexibility_score":      1,
    "deadline":               "2026-05-20",
    "issue_type":             "Task",
    "total_estimated_effort": 120,
}

# candidates = get_candidates(failed_task, 120)
#print(candidates)
candidates= [{'event_id': 'SCRUM-1', 'title': 'Research Capex', 'start_time': '2026-05-19T15:30:00+02:00', 'end_time': '2026-05-19T17:30:00+02:00', 'priority': 6, 'flexibility_score': 1, 'source': 'Jira'}, {'event_id': 'SCRUM-17', 'title': 'Documentation', 'start_time': '2026-05-20T11:45:00+02:00', 'end_time': '2026-05-20T16:45:00+02:00', 'priority': 5, 'flexibility_score': 1, 'source': 'Jira'}]

ideal = best_match(
    candidates,
    lambda c: c["flexibility_score"] == 1 and c["priority"] < failed_priority,
)


ideal = best_match2(
    candidates,failed_priority
)
print(ideal)