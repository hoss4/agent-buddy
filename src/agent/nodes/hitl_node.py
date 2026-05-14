from datetime import datetime, timezone
from src.database.db_utils import get_connection, log_audit_action_conn


def format_option(label: str, description: str) -> str:
    return f"  [{label}] {description}"


def present_conflict(signal: dict, conflict: dict, options: dict) -> str:
    """
    Prints the conflict to terminal and waits for user input.
    Returns the chosen option key.
    """
    print("\n" + "=" * 60)
    print("AGENT BUDDY — HUMAN DECISION REQUIRED")
    print("=" * 60)
    print(f"\n  Task:     {signal['title']}")
    print(f"  Conflict: '{conflict['title']}' (Fixed)")
    print(f"            {conflict['start_time']} → {conflict['end_time']}")
    print(f"\n  Options:")
    for key, desc in options.items():
        print(format_option(key, desc))
    print()

    while True:
        choice = input(f"  Your choice ({'/'.join(options.keys())}): ").strip().upper()
        if choice in options:
            return choice
        print(f"  Invalid choice. Please enter one of: {', '.join(options.keys())}")


def hitl_node(state: dict) -> dict:
    """
    Presents the Fixed conflict to the user and records their decision.
    The after_hitl_router in graph.py then routes based on hitl_decision.
    """
    print("-----------here---------------")
    signal   = state["current_signal"]
    print("-----------here1---------------")
    conflict = state.get("conflicting_event", {})
    print("-----------here2---------------")
    proposed = state.get("proposed_slot", {})
    print("-----------here3---------------")

    if conflict:
        # Fixed conflict case
        print(f"  Conflict: '{conflict['title']}' (Fixed)")
        print(f"            {conflict['start_time']} → {conflict['end_time']}")
        reason = f"Fixed conflict with '{conflict['title']}'"
    else:
        # Max retries case
        print(f"  Issue: Planner could not find a free slot")
        reason = "No available slot after 3 attempts"

    options = {
        "A": f"Skip '{signal['title']}' for now",
        "B": f"I will manually handle this",
        "C": f"Discard '{signal['title']}' entirely",
    }
    
    
    print(f"\n  Options:")
    for key, desc in options.items():
        print(f"  [{key}] {desc}")
    print()

    while True:
        choice = input(f"  Your choice (A/B/C): ").strip().upper()
        if choice in options:
            break
        print("  Invalid choice. Please enter A, B, or C.")

    decision_map = {"A": "skip", "B": "manual", "C": "discard"}
    decision = decision_map[choice]

    print(f"\n  [hitl] Decision recorded: {decision}")
    print("=" * 60 + "\n")
    
    
    print("-----------here6---------------")


    # Log the decision
    conn = get_connection()
    try:
        log_audit_action_conn(
            conn,
            signal["event_id"],
            "Conflict_Flagged",
            f"HITL decision for '{signal['title']}' vs Fixed '{conflict['title']}': {decision}",
        )
        # Mark as HITL_Resolved so it doesn't get reprocessed
        conn.cursor().execute("""
            UPDATE calendar_shadow
            SET status = 'HITL_Resolved'
            WHERE event_id = ?
        """, (signal["event_id"],))
        conn.commit()
    finally:
        conn.close()

    return {**state, "hitl_decision": decision}