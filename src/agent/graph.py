from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from src.agent.nodes.load_signal import load_signal_node
from src.agent.nodes.triage import triage_node
from src.agent.nodes.planner import planner_node
from src.agent.nodes.auditor import auditor_node
from src.agent.nodes.executor import executor_node
from src.agent.nodes.hitl_node  import hitl_node
from src.agent.state import AgentState


# class AgentState(TypedDict):
#     current_signal: dict
#     triage_decision: Optional[dict]
#     proposed_slot: Optional[dict]
#     conflict_found: bool
#     conflicting_event: Optional[dict]
#     retry_count: int
#     hitl_decision: Optional[str]
#     errors: list[str]
#     failed_slots: list[dict]


# ROUTERS (conditional edges)


def after_triage_router(state: AgentState) -> str:
    """After Triage: Jira tasks need a slot → Planner. Everything else → END."""
    decision = state.get("triage_decision")
    if not decision or decision.get("dismiss"):
        return END

    signal = state["current_signal"]

    # Only Jira tasks without a slot need the Planner
    if signal["source"] == "Jira" and not signal.get("start_time"):
        return "planner"

    # Calendar/Gmail events already have a slot — confirm them directly
    if signal.get("start_time"):
        return "executor"

    # Gmail event with extracted datetime from triage — send to executor
    if decision.get("extracted_start"):
        return "executor"

    # Calendar events already have a slot — nothing to plan
    return END

def after_auditor_router(state: dict) -> str:
    proposed    = state.get("proposed_slot")
    retry_count = state.get("retry_count", 0)

    # Planner exhausted retries
    if not proposed:
        print("  [router] Max retries — escalating to HITL.")
        return "hitl_gate"

    if not state.get("conflict_found"):
        return "executor"

    conflict = state.get("conflicting_event", {})

    if conflict.get("flexibility_score") == 1:
        # Flexible conflict — add to failed and retry
        failed_slots = state.get("failed_slots", [])
        failed_slots.append({
            "start":  state["proposed_slot"]["proposed_start"],
            "end":    state["proposed_slot"]["proposed_end"],
            "reason": f"Occupied by '{conflict['title']}'",
        })
        state["failed_slots"] = failed_slots
        print(f"  [router] Flexible conflict — retrying planner.")
        return "planner"

    # Fixed conflict — needs human
    print(f"  [router] Fixed conflict — escalating to HITL.")
    return "hitl_gate"


def after_hitl_router(state: dict) -> str:
    """Routes based on the human's HITL decision."""
    decision = state.get("hitl_decision")

    if decision == "skip":
        # Keep the conflicting event, park this task
        print("  [router] HITL: task skipped.")
        return END

    if decision == "manual":
        # User will handle it — just end the agent's involvement
        print("  [router] HITL: manual resolution chosen.")
        return END

    if decision == "discard":
        print("  [router] HITL: task discarded.")
        return END

    return END


# GRAPH

def build_graph():
    graph = StateGraph(AgentState)

    #add all nodes
    graph.add_node("load_signal", load_signal_node)
    graph.add_node("triage",triage_node)
    graph.add_node("planner", planner_node)
    graph.add_node("auditor", auditor_node)
    graph.add_node("executor", executor_node)
    graph.add_node("hitl_gate", hitl_node)

    #create graph
    graph.set_entry_point("load_signal")
    graph.add_edge("load_signal", "triage")
    # graph.add_edge("triage", END) 
    graph.add_conditional_edges(
        "triage", after_triage_router,{"planner" : "planner","executor": "executor",END : END,},
    )

    graph.add_edge("planner", "auditor")

    
    graph.add_conditional_edges(
        "auditor",
        after_auditor_router,
        {
            "executor":  "executor",
            "planner":   "planner",
            "hitl_gate": "hitl_gate",
        },
    )

    graph.add_conditional_edges(
        "hitl_gate",
        after_hitl_router,
        {END: END},
    )

    graph.add_edge("executor", END)

    

    return graph.compile()


#singleton — imported by the orchestrator
agent_buddy_graph = build_graph()