"""
agentx/server/api.py
=====================
FastAPI server for AgentX.

Wave 3 HITL endpoints added:
  POST /hitl/approve            - approve pending node (optionally override inputs)
  POST /hitl/reject             - reject pending node (with reason)
  POST /hitl/modify_node        - mutate pending node inputs while paused
  GET  /hitl/status/{user_id}   - inspect what's waiting for approval
"""

import asyncio
import json
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Any, Dict, Optional

from agentx.runtime.session import session_manager
from agentx.runtime.event_bus import bus, EVENTS
import agentx.config

app = FastAPI(title="AgentX Jarvis Server")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class TaskRequest(BaseModel):
    user_id: str
    task: str
    mode: str = "stable"

class InterruptRequest(BaseModel):
    user_id: str

class ResumeRequest(BaseModel):
    user_id: str

class ModifyRequest(BaseModel):
    user_id: str
    new_plan_data: dict

# --- Wave 3: HITL models ---

class ApproveRequest(BaseModel):
    user_id: str
    input_overrides: Optional[Dict[str, Any]] = None  # optional node input patches

class RejectRequest(BaseModel):
    user_id: str
    reason: str = "User rejected execution."

class ModifyNodeRequest(BaseModel):
    user_id: str
    field: str           # e.g. "task", "inputs", "uncertainty"
    value: Any           # new value for that field


# ---------------------------------------------------------------------------
# Core task endpoints
# ---------------------------------------------------------------------------

@app.post("/task")
async def submit_task(req: TaskRequest):
    agentx.config.AGENTX_DIVERSITY_BETA = (req.mode == "beta")
    session = session_manager.get_or_create(req.user_id)
    session.log_interaction("user", req.task)
    await task_queue.put({"session": session, "task": req.task})
    return {"status": "queued", "session_id": session.session_id, "mode": req.mode}

@app.post("/interrupt")
async def interrupt_task(req: InterruptRequest):
    session = session_manager.get_or_create(req.user_id)
    session.interrupt()
    return {"status": "interrupted"}

@app.post("/resume")
async def resume_task(req: ResumeRequest):
    session = session_manager.get_or_create(req.user_id)
    session.resume()
    return {"status": "resumed"}

@app.post("/modify")
async def modify_plan(req: ModifyRequest):
    session = session_manager.get_or_create(req.user_id)
    session.checkpoint = req.new_plan_data
    return {"status": "plan_modified"}

# In-memory queue for task processing
task_queue: asyncio.Queue = asyncio.Queue()


# ---------------------------------------------------------------------------
# Wave 3: HITL (Human-in-the-Loop) endpoints
# ---------------------------------------------------------------------------

@app.post("/hitl/approve")
async def hitl_approve(req: ApproveRequest):
    """
    Approve the pending node for a session.

    Optionally supply ``input_overrides`` to patch node fields before
    execution resumes (e.g. corrected file path, revised task string).
    """
    session = session_manager.get_or_create(req.user_id)
    node = session.pending_node

    if node is None:
        return {"status": "no_pending_node", "detail": "Nothing awaiting approval."}

    # Apply optional field overrides
    if req.input_overrides:
        for field, val in req.input_overrides.items():
            if hasattr(node, field):
                setattr(node, field, val)

    node.status = "RUNNING"
    session.pending_node = None
    session.resume()

    bus.publish(
        EVENTS.get("NODE_APPROVED", "NODE_APPROVED"),
        {"node_id": node.id, "overrides": req.input_overrides or {}}
    )

    return {
        "status": "approved",
        "node_id": node.id,
        "applied_overrides": req.input_overrides or {}
    }


@app.post("/hitl/reject")
async def hitl_reject(req: RejectRequest):
    """
    Reject the pending node. The executor will ESCALATE and stop execution.
    """
    session = session_manager.get_or_create(req.user_id)
    node = session.pending_node

    if node is None:
        return {"status": "no_pending_node", "detail": "Nothing awaiting rejection."}

    node.status = "FAILED"
    node.error = req.reason
    session.pending_node = None
    session.reject()   # unblocks executor; is_rejected=True causes it to exit

    bus.publish(
        EVENTS.get("NODE_REJECTED", "NODE_REJECTED"),
        {"node_id": node.id, "reason": req.reason}
    )

    return {"status": "rejected", "node_id": node.id, "reason": req.reason}


@app.post("/hitl/modify_node")
async def hitl_modify_node(req: ModifyNodeRequest):
    """
    Mutate a field on the pending node while execution is paused.
    Call before /hitl/approve to take effect.
    """
    session = session_manager.get_or_create(req.user_id)
    node = session.pending_node

    if node is None:
        return {"status": "no_pending_node"}

    if not hasattr(node, req.field):
        return {
            "status": "error",
            "detail": f"PlanNode has no field '{req.field}'."
        }

    old_val = getattr(node, req.field)
    setattr(node, req.field, req.value)

    return {
        "status": "modified",
        "node_id": node.id,
        "field": req.field,
        "old_value": old_val,
        "new_value": req.value,
    }


@app.get("/hitl/status/{user_id}")
async def hitl_status(user_id: str):
    """
    Return current HITL state for a user session.
    """
    if user_id not in session_manager.sessions:
        return {"status": "no_session"}

    session = session_manager.sessions[user_id]
    node = session.pending_node

    return {
        "user_id": user_id,
        "is_interrupted": session.is_interrupted,
        "is_rejected": session.is_rejected,
        "pending_node": {
            "id": node.id,
            "task": node.task,
            "risk": getattr(node, "risk", 0.0),
            "status": node.status,
        } if node else None,
    }


# ---------------------------------------------------------------------------
# WebSocket streaming
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


def get_event_loop():
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def broadcast_event(event_name, data):
    node = data if not isinstance(data, dict) else None
    node_id = data.get("node_id", "unknown") if isinstance(data, dict) else getattr(data, "id", "unknown")
    tool = getattr(data, "tool", "unknown") if not isinstance(data, dict) else "unknown"
    msg = json.dumps({"event": event_name, "node_id": node_id, "tool": tool})
    loop = get_event_loop()
    if loop and loop.is_running():
        asyncio.create_task(manager.broadcast(msg))


bus.subscribe(EVENTS["NODE_STARTED"], lambda n: broadcast_event("NODE_STARTED", n))
bus.subscribe(EVENTS["NODE_SUCCESS"], lambda n: broadcast_event("NODE_SUCCESS", n))
bus.subscribe(EVENTS["NODE_FAILED"],  lambda n: broadcast_event("NODE_FAILED", n))
bus.subscribe(EVENTS["ROLLBACK"],     lambda n: broadcast_event("ROLLBACK", n))
bus.subscribe(EVENTS["REPAIR"],       lambda n: broadcast_event("REPAIR", n))


# ---------------------------------------------------------------------------
# Telegram webhook
# ---------------------------------------------------------------------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    from agentx.scheduler.telegram import handle_telegram_message
    try:
        data = await request.json()
        if "message" in data:
            message = data["message"]
            chat_id = str(message["chat"]["id"])
            text = message.get("text", "")
            session = session_manager.get_or_create(chat_id)
            session.log_interaction("user", text)
            await handle_telegram_message(text, user_id=chat_id, session=session)
    except Exception as e:
        print(f"[API] Telegram webhook error: {e}")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard/failures")
async def get_failures_dashboard():
    try:
        from agentx.memory.failure_memory import failure_memory
        clusters = failure_memory.cluster_failures_by_embedding()
        analysis = failure_memory.analyze_failures()
        return {
            "status": "success",
            "clusters": clusters,
            "analysis": analysis,
            "total_failures_tracked": len(failure_memory.records),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
