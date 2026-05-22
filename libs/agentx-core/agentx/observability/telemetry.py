import os
import sys
import uuid
import json
import logging
from typing import Optional, Dict, Any
from contextvars import ContextVar
from datetime import datetime, timezone

# Context variable for trace ID
_trace_id_ctx: ContextVar[Optional[str]] = ContextVar("trace_id", default=None)

# Initialize standard Python logging
logger = logging.getLogger("agentx.telemetry")

class StructuredJSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        trace_id = get_trace_id()
        log_payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "trace_id": trace_id,
        }
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_payload)

class TraceLoggingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = get_trace_id()
        return super().format(record)

def configure_telemetry(log_format: Optional[str] = None, log_level: int = logging.INFO):
    """
    Configure high-fidelity telemetry formats.
    Support JSON for ingestion, or premium rich logs for local developers.
    """
    if not log_format:
        log_format = os.getenv("AGENTX_LOG_FORMAT", "rich").lower()

    root_logger = logging.getLogger("agentx")
    root_logger.setLevel(log_level)
    
    # Remove existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    if log_format == "json":
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        root_logger.addHandler(handler)
    else:
        # Standard Rich or human format
        try:
            from rich.logging import RichHandler
            handler = RichHandler(
                rich_tracebacks=True,
                markup=True,
                show_time=False,
                omit_repeated_times=True
            )
        except ImportError:
            handler = logging.StreamHandler(sys.stdout)
            fmt = TraceLoggingFormatter("[%(levelname)s] (%(name)s) [Trace: %(trace_id)s] %(message)s")
            handler.setFormatter(fmt)
        root_logger.addHandler(handler)

def get_trace_id() -> str:
    """Retrieve the current active trace ID, or generate one if not set."""
    tid = _trace_id_ctx.get()
    if not tid:
        tid = f"tr-{uuid.uuid4().hex[:12]}"
        _trace_id_ctx.set(tid)
    return tid

def set_trace_id(trace_id: Optional[str]) -> None:
    """Explicitly override the current active trace ID."""
    _trace_id_ctx.set(trace_id)

class TraceContextManager:
    """Context manager to scope a trace context cleanly."""
    def __init__(self, trace_id: Optional[str] = None):
        self.trace_id = trace_id or f"tr-{uuid.uuid4().hex[:12]}"
        self.token = None

    def __enter__(self):
        self.token = _trace_id_ctx.set(self.trace_id)
        return self.trace_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.token:
            _trace_id_ctx.reset(self.token)

def log_security_event(command: str, classification: Dict[str, Any], context: Optional[Dict[str, Any]] = None):
    """
    Log command security audits, generating high-fidelity structured logs
    for every blocked or audited terminal call.
    """
    trace_id = get_trace_id()
    decision = classification.get("decision", "allow")
    risk_level = classification.get("risk_level", "LOW")
    reasons = classification.get("reasons", [])

    log_payload = {
        "event": "security_audit",
        "trace_id": trace_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "command": command,
        "decision": decision,
        "risk_level": risk_level,
        "reasons": reasons,
        "context": context or {}
    }
    
    if decision == "deny":
        logger.error(
            "🛑 [Security Alert] Command BLOCKED: '%s' | Risk: %s | Reasons: %s", 
            command, risk_level, ", ".join(reasons)
        )
    elif decision == "ask":
        logger.warning(
            "⚠️ [Security Review Required] Command PENDING APPROVAL: '%s' | Risk: %s | Reasons: %s",
            command, risk_level, ", ".join(reasons)
        )
    else:
        logger.info("✅ [Security Audit] Command ALLOWED: '%s'", command)

    # Persist security log to .agentx/security_audit.log
    from agentx.config import PROJECT_ROOT
    audit_log_path = PROJECT_ROOT / ".agentx" / "security_audit.log"
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_payload) + "\n")
    except Exception:
        pass
