from src.database.db_utils import get_connection, log_audit_action_conn



def executor_node(state: dict) -> dict:
    signal   = state["current_signal"]
    proposed = state.get("proposed_slot")
    event_id = signal["event_id"]
    #source   = signal["source"]

    conn = get_connection()
    try:
        cursor = conn.cursor()

        if proposed:
            # Jira task — planner found a slot
            start = proposed["proposed_start"]
            end   = proposed["proposed_end"]

            cursor.execute("""
                UPDATE calendar_shadow
                SET start_time = ?,
                    end_time   = ?,
                    status     = 'Scheduled'
                WHERE event_id = ?
            """, (start, end, event_id))

            log_audit_action_conn(
                conn, event_id, "Scheduled",
                f"Planner scheduled '{signal['title']}' at {start} → {end}. "
                f"{proposed.get('reasoning', '')}",
            )
            conn.commit()
            print(f"  [executor]  SQLite updated: {signal['title']} → {start} to {end}")

            return {
                **state,
                "calendar_push": {
                    "title":       f"Deep Work: {signal['title']}",
                    "description": signal.get("description", ""),
                    "start":       start,
                    "end":         end,
                }
            }
           

    finally:
        conn.close()

    return state
