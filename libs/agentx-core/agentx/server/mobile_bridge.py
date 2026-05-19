from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from typing import List, Any
import asyncio
import json
from agentx.presence.state import get_system_state
from agentx.runtime.event_bus import bus, EVENTS

app = FastAPI(title="Agent Mobile Bridge")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        dead_connections = []
        # Iterate over a copy to avoid mutation errors (BUG-04)
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception as e:
                # Collect stale connections for removal (BUG-07)
                print(f"[Mobile Bridge] Broadcast failed for a connection: {e}")
                dead_connections.append(connection)
        
        for dead in dead_connections:
            if dead in self.active_connections:
                self.active_connections.remove(dead)

manager = ConnectionManager()

def make_serializable(obj: Any) -> Any:
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        return obj.to_dict()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return obj.model_dump()
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [make_serializable(x) for x in obj]
    return obj

def handle_event_bus_event(event_type: str, payload: Any):
    """Synchronous bridge to route global events to the active WebSocket broadcast pool."""
    message = json.dumps({
        "type": "event_broadcast",
        "event_type": event_type,
        "data": make_serializable(payload)
    })
    
    # Attempt to broadcast using all possible async context strategies
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
            return
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop_policy().get_event_loop()
        if loop.is_running():
            loop.create_task(manager.broadcast(message))
        else:
            loop.run_until_complete(manager.broadcast(message))
    except RuntimeError:
        try:
            asyncio.run(manager.broadcast(message))
        except Exception:
            pass
    except Exception:
        pass

# Subscribe manager dynamically to all core EventBus events
for et in EVENTS.values():
    bus.subscribe(et, lambda payload, event_type=et: handle_event_bus_event(event_type, payload))

@app.websocket("/ws/mobile")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Send current system state every 2 seconds
            state = get_system_state()
            await websocket.send_json({
                "type": "state_update",
                "data": state
            })
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

@app.post("/mobile/run")
async def mobile_run(objective: str):
    # This triggers a mission notification
    print(f"Mobile Mission Triggered: {objective}")
    await manager.broadcast(json.dumps({
        "type": "notification",
        "data": {"title": "Mission Started", "body": objective}
    }))
    return {"status": "queued", "objective": objective}

@app.get("/mobile/health")
async def health_check():
    return {"status": "alive", "engine": "Agent"}

def start_mobile_bridge(host="0.0.0.0", port=8001):
    import uvicorn
    uvicorn.run(app, host=host, port=port)
