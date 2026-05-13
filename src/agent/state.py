from typing import TypedDict, Optional

class AgentState(TypedDict):
    current_signal: dict
    triage_decision: Optional[dict]
    proposed_slot: Optional[dict]
    conflict_found: bool
    conflicting_event: Optional[dict]
    retry_count: int
    hitl_decision: Optional[str]
    errors: list[str]
    failed_slots: list[dict]