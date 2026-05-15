from agentx.observability.trace import trace_store

def replay(plan_id: str):
    """
    Step through past traces to replay execution state visually.
    Useful for debugging and observability.
    """
    traces = trace_store.load(plan_id)
    if not traces:
        print(f"[Replay] No traces found for plan_id: {plan_id}")
        return

    print(f"\n--- Replaying Execution Trace for Plan: {plan_id} ---")
    for event in traces:
        # Simulate execution progression
        print(f"[{event['timestamp']}] {event['event']} | Node: {event['node_id']} ({event['tool']})")
        if event["event"] == "NODE_FAILED":
            print(f"  -> State snapshot available: {len(event['state'].keys())} keys")
    print("--- Replay Complete ---\n")
