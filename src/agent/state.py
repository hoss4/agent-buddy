from typing import TypedDict, Optional

class AgentState(TypedDict):
    current_signal: dict
    triage_decision: Optional[dict]
    proposed_slot: Optional[dict]
    resolver_outcome: Optional[str]
    swap_candidate: Optional[dict]
    hitl_candidates: Optional[list]
    hitl_decision: Optional[str]
    errors: list[str]
    failed_slots: list[dict]
    calendar_push: Optional[dict]
    calendar_delete: Optional[str] 