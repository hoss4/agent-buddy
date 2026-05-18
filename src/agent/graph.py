from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from src.agent.nodes.triage import triage_node
# from src.agent.nodes.planner import planner_node
# from src.agent.nodes.auditor import auditor_node
from src.agent.nodes.executor import executor_node
from src.agent.nodes.hitl_node  import hitl_node
from src.agent.nodes.scheduler import scheduler_node
from src.agent.nodes.resolver import resolver_node

from src.agent.state import AgentState




# ROUTERS


def after_triage_router(state: AgentState) -> str:
    """After Triage: Jira tasks need a slot → Planner. Everything else → END."""
    decision = state.get("triage_decision")
    if not decision or decision.get("dismiss"):
        return END

    signal = state["current_signal"]

    if signal["source"] == "Jira" :
        return "scheduler"
        
    if signal["source"] == "Google_Calendar":
        return END
    
    return END

def after_scheduler_router(state: dict) -> str:
    if not state.get("proposed_slot"):
        return "resolver"
    return "executor"

def after_resolver_router(state: dict) -> str:
    outcome = state.get("resolver_outcome")
    if outcome == "auto_swap":
        return "executor"
    return "hitl_gate"

def after_hitl_router(state: dict) -> str:
    if state.get("calendar_push"):
        return "executor"
    return END

# def after_auditor_router(state: dict) -> str:
#     proposed    = state.get("proposed_slot")
#     retry_count = state.get("retry_count", 0)

#     # planner exhausted retries or didn't propose
#     if not proposed:
#         print("  [router] Max retries — escalating to HITL.")
#         return "hitl_gate"

#     # no conflict can be scheduled
#     if not state.get("conflict_found"):
#         return "executor"

    
#     # check if both are Fixed — only then go to HITL immediately
#     signal_flex   = state["current_signal"].get("flexibility_score", 1)
#     conflict_flex = state.get("conflicting_event", {}).get("flexibility_score", 1)

#     if signal_flex == 0 and conflict_flex == 0:
#         print("  [router] Fixed vs Fixed conflict — escalating to HITL.")
#         return "hitl_gate"

#     # otherwise retry planner
#     failed_slots = state.get("failed_slots", [])
#     failed_slots.append({
#         "start":  state["proposed_slot"]["proposed_start"],
#         "end":    state["proposed_slot"]["proposed_end"],
#         "reason": f"Occupied by '{state['conflicting_event']['title']}'",
#     })
#     state["failed_slots"] = failed_slots
#     print("  [router] Conflict — retrying planner.")
#     return "planner"



# def after_hitl_router(state: dict) -> str:
#     """Routes based on the human's HITL decision."""
#     decision = state.get("hitl_decision")

#     if decision == "skip":
#         # Keep the conflicting event, park this task
#         print("  [router] HITL: task skipped.")
#         return END

#     if decision == "manual":
#         # User will handle it — just end the agent's involvement
#         print("  [router] HITL: manual resolution chosen.")
#         return END

#     if decision == "discard":
#         print("  [router] HITL: task discarded.")
#         return END

#     return END


# GRAPH

def build_graph():
    
    graph = StateGraph(AgentState)

    # add all nodes
    
    graph.add_node("triage",triage_node)
    # graph.add_node("planner", planner_node)
    # graph.add_node("auditor", auditor_node)
    graph.add_node("executor", executor_node)
    graph.add_node("hitl_gate", hitl_node)
    graph.add_node("scheduler",scheduler_node)
    graph.add_node("resolver", resolver_node)

    # create graph

    graph.set_entry_point("triage")
    
  
    graph.add_conditional_edges(
        "triage", 
        after_triage_router,{
            "scheduler":"scheduler",
            END : END,},
    )

    # graph.add_edge("planner", "auditor")

    
    # graph.add_conditional_edges(
    #     "auditor",
    #     after_auditor_router,
    #     {
    #         "executor":  "executor",
    #         "planner":   "planner",
    #         "hitl_gate": "hitl_gate"
    #     },
    # )
    
    # graph.add_conditional_edges(
    #     "scheduler",
    #     after_auditor_router,
    #     {
    #         "executor":  "executor",
    #         "hitl_gate": "hitl_gate"
    #     },
    # )
    
    graph.add_conditional_edges(
        "scheduler",
        after_scheduler_router,
        {
            "resolver":"resolver",
            "executor":"executor"
        }
    )
    
    graph.add_conditional_edges(
        "resolver",
        after_resolver_router,
        {
            "hitl_gate":"hitl_gate",
            "executor":"executor"
        }
    )
    
    graph.add_conditional_edges(
        "hitl_gate",
        after_hitl_router,
        {
            "executor": "executor",
            END: END
        }
    )
    

    # graph.add_conditional_edges(
    #     "hitl_gate",
    #     after_hitl_router,
    #     {END: END},
    # )

    graph.add_edge("executor", END)

    

    return graph.compile()


agent_buddy_graph = build_graph()