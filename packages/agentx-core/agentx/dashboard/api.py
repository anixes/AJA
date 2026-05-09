import os
from fastapi import FastAPI, HTTPException
from typing import Dict, Any, List
from agentx.observability.trace import trace_store, TRACE_DIR

app = FastAPI(title="AgentX Observability Dashboard")

@app.get("/plans")
async def get_plans():
    """Return list of all executed plan IDs."""
    if not os.path.exists(TRACE_DIR):
        return {"plans": []}
    
    files = os.listdir(TRACE_DIR)
    plans = [f.replace("trace_", "").replace(".json", "") for f in files if f.startswith("trace_")]
    return {"plans": plans}

@app.get("/trace/{plan_id}")
async def get_trace(plan_id: str):
    """Return trace events for a specific plan."""
    trace = trace_store.load(plan_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return {"plan_id": plan_id, "events": trace}

@app.get("/metrics")
async def get_metrics():
    """Return system-wide metrics."""
    try:
        from agentx.observability.metrics import metrics_system
        return metrics_system.get_summary()
    except Exception:
        return {"error": "Metrics system unavailable"}

@app.post("/replay/{plan_id}")
async def replay_plan(plan_id: str):
    """Deterministically replay a plan from trace."""
    # Simulation of replay logic. This would instantiate a special replayer.
    return {"status": "replay_started", "plan_id": plan_id}

# A simple HTML frontend could be served here if needed.
