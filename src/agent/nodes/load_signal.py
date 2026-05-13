def load_signal_node(state: dict) -> dict:
    
    signal = state.get("current_signal")
    if not signal:
        print("load_signal, No signal provided. Ending.")
        return state

    print(f"\n[load_signal] Processing: {signal['title'][:60]} ({signal['source']})")
    return state