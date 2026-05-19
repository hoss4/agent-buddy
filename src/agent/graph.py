from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from src.agent.nodes.triage import triage_node
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




# GRAPH

def build_graph():
    
    graph = StateGraph(AgentState)

    # add all nodes
    
    graph.add_node("triage",triage_node)
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