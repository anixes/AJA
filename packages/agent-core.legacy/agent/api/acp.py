import sys
import json
import uuid
import threading
import queue
from typing import Dict, Any, Optional, Callable, List, Union
from pydantic import BaseModel

class ACPMessage(BaseModel):
    jsonrpc: str = "2.0"
    id: Optional[Union[str, int]] = None
    method: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

class ACPBridge:
    """
    Standardized JSON-over-stdio communication layer for Agent.
    Enables Agent to act as an Orchestrator of Orchestrators.
    """
    def __init__(self, input_stream=sys.stdin, output_stream=sys.stdout):
        self.input_stream = input_stream
        self.output_stream = output_stream
        self.handlers: Dict[str, Callable] = {}
        self.pending_requests: Dict[str, queue.Queue] = {}
        self._stop_event = threading.Event()

    def register_handler(self, method: str, handler: Callable):
        self.handlers[method] = handler

    def send_notification(self, method: str, params: Dict[str, Any]):
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        self._write(msg)

    def call_method(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Any:
        req_id = str(uuid.uuid4())
        q = queue.Queue()
        self.pending_requests[req_id] = q
        
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }
        self._write(msg)
        
        try:
            return q.get(timeout=timeout)
        finally:
            del self.pending_requests[req_id]

    def _write(self, msg: Dict[str, Any]):
        json.dump(msg, self.output_stream)
        self.output_stream.write("\n")
        self.output_stream.flush()

    def listen(self):
        """Main loop for reading from input stream."""
        while not self._stop_event.is_set():
            line = self.input_stream.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
                self._handle_message(msg)
            except json.JSONDecodeError:
                continue

    def _handle_message(self, msg: Dict[str, Any]):
        msg_id = msg.get("id")
        method = msg.get("method")
        
        if method:
            # Request or Notification
            handler = self.handlers.get(method)
            if handler:
                result = handler(msg.get("params", {}))
                if msg_id:
                    # Send response back if it's a request
                    self._write({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": result
                    })
        elif msg_id:
            # Response
            q = self.pending_requests.get(msg_id)
            if q:
                if "result" in msg:
                    q.put(msg["result"])
                elif "error" in msg:
                    q.put(Exception(msg["error"].get("message", "Unknown error")))

    def stop(self):
        self._stop_event.set()

# ACP Client Implementation
class ACPClient:
    def __init__(self, bridge: ACPBridge):
        self.bridge = bridge

    def initialize(self, agent_id: str, capabilities: List[str]):
        return self.bridge.call_method("initialize", {
            "agentId": agent_id,
            "capabilities": capabilities,
            "protocolVersion": "v1"
        })

    def prompt(self, session_id: str, prompt: str):
        return self.bridge.call_method("prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": prompt}]
        })
