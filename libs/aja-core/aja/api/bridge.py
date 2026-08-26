import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from aja.api.app_context import (
    AppContext,
    get_app_context as _get_app_context,  # noqa: F401
    set_app_context,  # noqa: F401
)
from aja.api.routes import attach_route_groups
from aja.api.services import approval_service as _approval_service
from aja.api.services.approval_service import (  # noqa: E402,F401
    analyze_shell_command,
    approval_is_expired,
    build_approval_object,
    build_dry_run_summary,
    build_rollback_path,
    format_approval_for_mobile,
    normalize_risk_level,
    run_file_guardian_check,
    run_shell_command,
)


def get_pending_approval_by_id(request_id: str):
    return _approval_service.get_pending_approval_by_id(
        request_id,
        memory_provider=lambda: get_aja_memory(),
    )


async def approve_runtime_approval(request_id: str, user_id: int | None = None):
    return await _approval_service.approve_runtime_approval(
        request_id,
        user_id=user_id,
        memory_provider=lambda: get_aja_memory(),
        guardian_runner=lambda cmd: run_file_guardian_check(cmd),
        shell_runner=lambda cmd: run_shell_command(cmd),
        timeout=TELEGRAM_COMMAND_TIMEOUT,
    )


def reject_runtime_approval(request_id: str, user_id: int | None = None):
    return _approval_service.reject_runtime_approval(
        request_id,
        user_id=user_id,
        memory_provider=lambda: get_aja_memory(),
    )
from aja.api.services.command_policy import (
    analyze_shell_command as analyze_shell_command_policy,
)
from aja.api.services.config_store import (  # noqa: E402,F401
    load_config as _load_config_store,
    mask_api_key,
    save_config as _save_config_store,
)
from aja.api.services.legacy_dashboard import dashboard_unavailable_payload
from aja.api.services.telegram_gateway import (  # noqa: E402,F401
    _escape_mdv2,
    build_aja_chat_context,
    build_aja_help,
    build_supported_command,
    deliver_executive_review,
    execute_aja_command_sync,
    format_status_for_mobile,
    generate_aja_chat_reply,
    generate_definition_of_done,
    get_telegram_message,
    seconds_until_tonight,
    send_communication_if_supported,
    send_telegram_message,
)
from aja.config import DATA_DIR, PROJECT_ROOT
from aja.memory.secretary import (
    AJAMemory,
    format_communication_for_mobile,
    format_tasks_for_mobile,
    get_aja_memory,
    parse_communication_intent,
    parse_task_intent,
)
from aja.gateway.auth import get_allowlist, is_user_authorized
from aja.utils.maintenance import run_maintenance
from aja.utils.redact import redact_secrets


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Launch Telegram Poller if token is available. Opt-out for
    # tests/headless embeds: a second poller steals Telegram getUpdates
    # from the live gateway (Conflict -> dropped messages).
    background_disabled = bool(os.getenv("AJA_BRIDGE_BACKGROUND_DISABLED"))
    polling_task = None
    if TELEGRAM_BOT_TOKEN and not background_disabled:
        print(f"[*] AJA Voice Gateway: Initializing Telegram Poller...")
        polling_task = asyncio.create_task(telegram_polling_loop())

    # Launch Maintenance Service in a background thread. Opt out with
    # AJA_BRIDGE_BACKGROUND_DISABLED=1 (tests, aja serve where the scheduler
    # already owns periodic pruning).
    if not background_disabled:
        print(f"[*] AJA Core: Initializing Maintenance Service...")
        maintenance_thread = threading.Thread(target=run_maintenance, daemon=True)
        maintenance_thread.start()

    yield

    # Shutdown
    if polling_task:
        print(f"[*] AJA Voice Gateway: Stopping Telegram Poller...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass


app = FastAPI(lifespan=lifespan)
attach_route_groups(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:5176",
        "http://127.0.0.1:5176",
        "http://localhost:5177",
        "http://127.0.0.1:5177",
        "http://localhost:5178",
        "http://127.0.0.1:5178",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(STATIC_DIR), html=True), name="app")


@app.get("/", include_in_schema=False)
def root_entrypoint():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"status": "ok", "app": "/app"}


# Bridge constants are aliases of the AppContext snapshot so services and
# routes read one source of truth while preserving import-time env reads.
_ctx = _get_app_context()
RUNTIME_STATE_PATH = _ctx.runtime_state_path  # debug export only
BATON_DIR = _ctx.baton_dir
DEFAULT_API_TOKEN = _ctx.default_api_token
API_TOKEN = _ctx.api_token
TELEGRAM_HISTORY_PATH = _ctx.history_path
TELEGRAM_PENDING_PATH = _ctx.pending_path  # debug export only
APPROVAL_AUDIT_PATH = _ctx.audit_path  # debug export only
TELEGRAM_BOT_TOKEN = _ctx.telegram_bot_token
TELEGRAM_ALLOWED_USER_ID = _ctx.telegram_allowed_user_id
TELEGRAM_WEBHOOK_SECRET = _ctx.telegram_webhook_secret
TELEGRAM_COMMAND_TIMEOUT = _ctx.telegram_command_timeout
AJA_MEMORY_DIR = DATA_DIR / "lancedb"

logger = logging.getLogger(__name__)

DENY_BINARIES = {
    "dd": "Low-level disk writes can irreversibly destroy data.",
    "mkfs": "Filesystem formatting is blocked.",
    "format": "Filesystem formatting is blocked.",
    "diskpart": "Disk partition manipulation is blocked.",
    "bcdedit": "Boot configuration changes are blocked.",
}

ASK_BINARIES = {
    "shutdown": "System shutdown requires confirmation.",
    "reboot": "System restart requires confirmation.",
    "taskkill": "Process termination requires confirmation.",
    "powershell": "PowerShell execution requires confirmation.",
    "pwsh": "PowerShell execution requires confirmation.",
    "python": "Interpreter execution can run arbitrary code.",
    "python3": "Interpreter execution can run arbitrary code.",
    "node": "Interpreter execution can run arbitrary code.",
    "git": "Git commands can mutate the workspace.",
    "npm": "Package manager commands can mutate the workspace.",
    "pnpm": "Package manager commands can mutate the workspace.",
    "yarn": "Package manager commands can mutate the workspace.",
}

DENY_PATTERNS = {
    "network-pipe": "Piping network output directly into an interpreter is blocked.",
    "ssh-write": "Writing directly into SSH trust material is blocked.",
    "system-path-write": "Redirecting output into protected system paths is blocked.",
    "command-substitution": "Shell substitution syntax can hide unsafe behavior.",
    "unbalanced-shell-syntax": "Command parsing failed due to invalid shell syntax.",
}

ASK_PATTERNS = {
    "protected-path": "The command targets a protected path.",
    "path-traversal": "The command uses parent-directory traversal.",
    "recursive-delete-flag": "The command includes recursive destructive flags.",
}


def verify_token(authorization: str = Header(None)):
    if not authorization or authorization.replace("Bearer ", "") != API_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


def is_loopback_host(host: str) -> bool:
    return host in ("127.0.0.1", "localhost", "::1")


def resolve_bridge_bind():
    """Resolve the bridge bind address.

    Binds 0.0.0.0 by default to allow local network / phone connectivity.
    """
    host = os.getenv("AJA_BRIDGE_HOST", "0.0.0.0")
    port = int(os.getenv("AJA_BRIDGE_PORT", "8000"))
    if not is_loopback_host(host) and API_TOKEN == DEFAULT_API_TOKEN:
        logger.warning(
            "AJA bridge is bound to %s with default token. Set AJA_API_TOKEN for production security.",
            host,
        )
    return host, port



from aja.presence.state import get_system_state
from aja.runtime.event_bus import bus, EVENTS


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.queues: list[asyncio.Queue] = []

    async def connect(self, websocket: WebSocket) -> asyncio.Queue:
        await websocket.accept()
        self.active_connections.append(websocket)
        q: asyncio.Queue = asyncio.Queue()
        self.queues.append(q)
        return q

    def disconnect(self, websocket: WebSocket, q: asyncio.Queue | None = None):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if q is not None and q in self.queues:
            self.queues.remove(q)

    async def broadcast(self, message: str):
        dead_connections = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                dead_connections.append(connection)
        for dead in dead_connections:
            self.disconnect(dead)

    def broadcast_event(self, event_type: str, data: Any):
        msg = {"type": "event_broadcast", "event_type": event_type, "data": data}
        for q in list(self.queues):
            try:
                q.put_nowait(msg)
            except Exception as e:
                logger.warning("WS broadcast dropped event %s to a full/closed queue: %s", event_type, e)


ws_manager = ConnectionManager()


def _setup_event_bus_subscriptions():
    for event_name, event_type in EVENTS.items():
        def make_handler(etype):
            def handler(payload):
                ws_manager.broadcast_event(etype, payload)
            return handler
        bus.subscribe_once(event_type, make_handler(event_type), key=f"ws_manager:{event_type}")


_setup_event_bus_subscriptions()


def _ws_token_ok(websocket: WebSocket) -> bool:
    """Authenticate a websocket via ?token= query param or Bearer header."""
    provided = websocket.query_params.get("token") or ""
    auth_header = websocket.headers.get("authorization") or ""
    bearer = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    return bool(API_TOKEN) and API_TOKEN in (provided, bearer)


@app.get("/health")
def health_probe():
    """Minimal unauthenticated liveness probe (no system details)."""
    return {"ok": True}


@app.websocket("/ws/mobile")
async def websocket_endpoint(websocket: WebSocket):
    if not _ws_token_ok(websocket):
        await websocket.close(code=4401)
        return
    q = await ws_manager.connect(websocket)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=2.0)
                await websocket.send_json(msg)
            except asyncio.TimeoutError:
                state = get_system_state()
                await websocket.send_json({"type": "state_update", "data": state})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, q)
    except Exception:
        ws_manager.disconnect(websocket, q)


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_telegram_history(event: dict):
    TELEGRAM_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"created_at": now_iso(), **event}
    with TELEGRAM_HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def append_approval_audit(event: dict):
    """Persist an approval audit entry to LanceDB (authoritative) and JSONL (debug export)."""
    get_aja_memory().log_approval_audit(
        {
            "approval_id": event.get("id", "unknown"),
            "action": event.get("action", "unknown"),
            "requester_source": event.get("requester_source"),
            "command": event.get("command"),
            "risk_level": event.get("risk_level"),
            "reasons": event.get("reasons"),
            "exit_code": event.get("exit_code"),
            "note": event.get("note"),
        }
    )
    # Debug export to JSONL (optional, non-authoritative)
    try:
        APPROVAL_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"created_at": now_iso(), **event}
        with APPROVAL_AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    except Exception as e:
        logger.warning("Failed to append approval audit entry: %s", e)


def save_runtime_state(state: dict):
    """Write a debug snapshot of runtime state to JSON. Not authoritative — LanceDB is."""
    try:
        RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write runtime state snapshot: %s", e)


def add_runtime_event(event: dict):
    """Append a runtime event to LanceDB (authoritative source of truth)."""
    get_aja_memory().add_runtime_event(
        {
            "event_type": event.get("type", "INFO"),
            "tool": event.get("tool"),
            "message": event.get("message", ""),
            "command": event.get("command"),
            "root_binary": event.get("rootBinary"),
            "level": event.get("level"),
            "metadata": {
                k: v
                for k, v in event.items()
                if k
                not in {"type", "tool", "message", "command", "rootBinary", "level"}
            },
        }
    )


def set_runtime_pending_approval(approval: dict | None):
    """Mark pending approval resolved (None) or write a new approval row to LanceDB."""
    if approval is None:
        # Expire any remaining 'pending' rows (belt-and-suspenders)
        mem = get_aja_memory()
        active = mem.get_active_approval()
        if active:
            mem.update_approval(active["approval_id"], "resolved", "Cleared by system.")
    else:
        # The canonical write happens in create_approval_in_db; this is a no-op guard.
        pass


def create_approval_in_db(approval: dict) -> str:
    """Persist a new approval object to LanceDB and return the approval_id."""
    return get_aja_memory().create_approval(
        {
            "approval_id": approval.get("id"),
            "tool": approval.get("tool", "bash"),
            "command": approval.get("command"),
            "command_preview": approval.get("commandPreview")
            or approval.get("command"),
            "action_type": approval.get("actionType"),
            "root_binary": approval.get("rootBinary"),
            "risk_level": approval.get("riskLevel", "medium"),
            "level": approval.get("level"),
            "reasons": approval.get("reasons", []),
            "operator_reason": approval.get("operatorReason"),
            "rollback_path": approval.get("rollbackPath"),
            "dry_run_summary": approval.get("dryRunSummary"),
            "requester_source": approval.get("requesterSource", "CLI"),
            "telegram_meta": approval.get("telegram") or {},
            "expires_at": approval.get("expiresAt"),
        }
    )


def load_telegram_pending():
    """Returns active pending approvals keyed by approval_id (read from LanceDB)."""
    active = get_aja_memory().get_active_approval()
    if not active:
        return {}
    return {active["approval_id"]: active}


def save_telegram_pending(data: dict):
    """No-op: Telegram approvals now live in aja_approvals table."""
    # Debug export only
    try:
        TELEGRAM_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        TELEGRAM_PENDING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to write telegram pending debug export: %s", e)


def compact_text(value: str, limit: int = 1800):
    text = (value or "").strip()
    if not text:
        return "(no output)"
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + "\n\n... output trimmed for Telegram."


def resolve_npx_executable():
    return shutil.which("npx") or shutil.which("npx.cmd") or "npx"


# get_secretary_memory is now imported from aja.memory.secretary





def ensure_telegram_secret(secret_header: str | None):
    if TELEGRAM_WEBHOOK_SECRET and secret_header != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")





async def execute_telegram_command(text: str, user_id: int, chat_id: int | str):
    pending = load_telegram_pending()
    normalized = " ".join((text or "").strip().split())
    lower = normalized.lower()

    if lower.startswith("/handover "):
        otc = normalized.split(maxsplit=1)[1].strip()
        try:
            from aja.orchestration.handover import HandoverManager

            manager = HandoverManager()
            session_data = manager.resolve_otc(otc)
            if session_data:
                # Update whitelist if this was an anonymous handover
                global TELEGRAM_ALLOWED_USER_ID
                TELEGRAM_ALLOWED_USER_ID = str(user_id)
                append_telegram_history(
                    {
                        "user_id": user_id,
                        "chat_id": chat_id,
                        "command": text,
                        "decision": "handover_success",
                        "session_id": session_data.get("session_id"),
                    }
                )
                return f"🚀 Handover Successful!\nAJA is now linked to terminal session: {session_data.get('session_id')}\nYou are now authorized to command this instance."
            else:
                return "❌ Invalid or expired OTC. Please generate a new one from your terminal using 'aja-handover'."
        except Exception as e:
            return f"❌ Handover failed: {e}"

    if lower.startswith("approve message ") or lower.startswith("reject message "):
        aja_reply = await asyncio.to_thread(
            execute_aja_command_sync, text, "Telegram", f"telegram:{user_id}"
        )
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "communication_approval",
            }
        )
        return aja_reply or "Unable to update message approval."

    if lower.startswith("reject ") or lower.startswith("deny "):
        request_id = normalized.split(maxsplit=1)[1].strip()
        result = reject_runtime_approval(request_id, user_id)
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "approval_rejected",
                "approval_id": request_id,
            }
        )
        return result["message"]

    if lower.startswith("approve ") or lower.startswith("confirm "):
        request_id = normalized.split(maxsplit=1)[1].strip()
        result = await approve_runtime_approval(request_id, user_id)
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "approval_approved" if result["ok"] else "approval_failed",
                "approval_id": request_id,
            }
        )
        return result["message"]

    if lower.startswith("send message "):
        message_id = normalized.split(maxsplit=2)[2].strip()
        message = await asyncio.to_thread(
            get_aja_memory().get_communication, message_id
        )
        if not message:
            return f"No message found for {message_id}."
        result = await send_communication_if_supported(message)
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "communication_send"
                if result["ok"]
                else "communication_send_failed",
                "message_id": message_id,
            }
        )
        return result["message"]

    spec = build_supported_command(text)
    if spec["kind"] == "help":
        return spec["message"]
    if spec["kind"] == "status":
        payload = await asyncio.to_thread(build_status_payload)
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "status",
            }
        )
        return format_status_for_mobile(payload)

    # Only if it's not a hardcoded command or status/help, try AJA
    if spec["kind"] == "chat":
        aja_reply = await asyncio.to_thread(
            execute_aja_command_sync, text, "Telegram", f"telegram:{user_id}"
        )
        if aja_reply:
            append_telegram_history(
                {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "command": text,
                    "decision": "aja_memory",
                }
            )
            return aja_reply

        # Fallback to general AI chat
        reply = await asyncio.to_thread(generate_aja_chat_reply, text, user_id, chat_id)
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "aja_chat",
            }
        )
        return reply

    # If we are here, it's an 'execute' kind from build_supported_command
    command = spec["command"]
    file_guardian = await run_file_guardian_check(command)
    if file_guardian["decision"] == "DENY":
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "mapped_command": command,
                "decision": "blocked_by_file_guardian",
                "error": file_guardian.get("error"),
            }
        )
        detail = f": {file_guardian['error']}" if file_guardian.get("error") else "."
        return f"Command denied by FileGuardian{detail}"

    classification = analyze_shell_command(command)
    if classification["decision"] == "deny":
        reason_text = "\n".join(f"- {reason}" for reason in classification["reasons"])
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "mapped_command": command,
                "decision": "blocked",
                "reasons": classification["reasons"],
            }
        )
        return f"Command denied by AJA Safety Gate.\n{reason_text}"

    if (
        spec.get("requires_confirmation")
        or classification["decision"] == "ask"
        or file_guardian["decision"] == "ASK"
    ):
        if file_guardian["decision"] == "ASK":
            classification["reasons"].append(
                "FileGuardian requested review before execution."
            )
        approval = build_approval_object(
            text, command, spec, classification, user_id, chat_id
        )
        # --- AJA Brain: persist approval to LanceDB (single source of truth) ---
        create_approval_in_db(approval)
        mem = get_aja_memory()
        mem.add_runtime_event(
            {
                "event_type": "ASK",
                "tool": "bash",
                "message": approval["operatorReason"],
                "command": command,
                "root_binary": approval.get("rootBinary"),
                "level": approval.get("level"),
            }
        )
        mem.log_approval_audit(
            {
                "approval_id": approval["id"],
                "action": "requested",
                "requester_source": "Telegram",
                "command": command,
                "risk_level": approval["riskLevel"],
            }
        )
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "mapped_command": command,
                "decision": "approval_requested",
                "approval_id": approval["id"],
                "reasons": approval["reasons"],
            }
        )
        return format_approval_for_mobile(approval)

    result = await run_shell_command(command)
    append_telegram_history(
        {
            "user_id": user_id,
            "chat_id": chat_id,
            "command": text,
            "mapped_command": command,
            "decision": "executed",
            "exit_code": result["code"],
        }
    )
    prefix = "OK" if result["ok"] else f"Failed ({result['code']})"
    return f"{prefix}: {text}\n{result['output']}"


def run_runtime_action(action: str):
    try:
        result = subprocess.run(
            ["npx", "tsx", "src/runtime_actions.ts", action],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to launch runtime action: {exc}"
        ) from exc

    payload_raw = (result.stdout or result.stderr).strip()
    try:
        payload = (
            json.loads(payload_raw)
            if payload_raw
            else {"ok": result.returncode == 0, "message": ""}
        )
    except json.JSONDecodeError:
        payload = {"ok": result.returncode == 0, "message": payload_raw}

    if result.returncode != 0:
        raise HTTPException(
            status_code=500, detail=payload.get("message") or "Runtime action failed."
        )

    return payload


def load_runtime_state():
    """Build a legacy-compatible runtime state dict from LanceDB (AJA Brain)."""
    mem = get_aja_memory()
    pending_row = mem.get_active_approval()
    events = mem.get_runtime_events(50)
    # Convert DB rows to the legacy shape the dashboard and snapshot builder expect
    pending = None
    if pending_row:
        pending = {
            "id": pending_row.get("approval_id"),
            "tool": pending_row.get("tool"),
            "command": pending_row.get("command"),
            "commandPreview": pending_row.get("command_preview")
            or pending_row.get("command"),
            "actionType": pending_row.get("action_type"),
            "rootBinary": pending_row.get("root_binary"),
            "riskLevel": pending_row.get("risk_level"),
            "level": pending_row.get("level"),
            "reasons": pending_row.get("reasons", []),
            "operatorReason": pending_row.get("operator_reason"),
            "rollbackPath": pending_row.get("rollback_path"),
            "dryRunSummary": pending_row.get("dry_run_summary"),
            "requesterSource": pending_row.get("requester_source"),
            "expiresAt": pending_row.get("expires_at"),
            "createdAt": pending_row.get("created_at"),
            "telegram": pending_row.get("telegram_meta") or {},
        }
    formatted_events = [
        {
            "id": e.get("event_id"),
            "type": e.get("event_type"),
            "tool": e.get("tool"),
            "message": e.get("message"),
            "command": e.get("command"),
            "rootBinary": e.get("root_binary"),
            "level": e.get("level"),
            "createdAt": e.get("created_at"),
        }
        for e in events
    ]
    return {"pendingApproval": pending, "events": formatted_events, "tokenStats": None}


def build_status_payload(runtime_state=None):
    territories = []
    monitored_paths = ["src/prod", "src/vault", "src/tools"]
    runtime_state = runtime_state or load_runtime_state()

    for folder in monitored_paths:
        path = Path(folder)
        baton = path / ".baton"
        status = "healing" if baton.exists() else "stable"
        file_count = len(list(path.glob("*"))) if path.exists() else 0
        load = (file_count * 15) % 100

        territories.append(
            {
                "name": folder,
                "status": status,
                "load": f"{load}%",
            }
        )

    pending = runtime_state.get("pendingApproval")
    return {
        "territories": territories,
        "total_files": sum(len(files) for _, _, files in os.walk("src"))
        if Path("src").exists()
        else 0,
        "active_agents": len(territories),
        "safety_alerts": 1 if pending else 0,
        "pending_approval": pending,
        "baton_count": len(load_baton_state()),
        "token_stats": runtime_state.get("tokenStats"),
    }


def load_baton_state():
    if not BATON_DIR.exists():
        return []

    batons = []
    for baton_file in sorted(BATON_DIR.glob("*.json")):
        try:
            baton = json.loads(baton_file.read_text(encoding="utf-8"))
            baton["file"] = baton_file.name
            baton["history_count"] = len(baton.get("history", []))

            # Extract live telemetry
            baton["progress"] = baton.get("progress", 0)
            baton["last_pulse"] = baton.get("updated_at", time.time())

            batons.append(baton)
        except Exception:
            batons.append(
                {
                    "file": baton_file.name,
                    "status": "invalid",
                    "task": baton_file.stem,
                    "error": "Unable to parse baton file.",
                }
            )

    return batons


def build_runtime_snapshot():
    runtime_state = load_runtime_state()
    return {
        "status": build_status_payload(runtime_state),
        "events": runtime_state.get("events", [])[:10],
        "diff": get_diff().get("diff"),
        "history": get_git_history().get("commits", []),
        "batons": load_baton_state(),
    }


@app.get("/status", dependencies=[Depends(verify_token)])
def get_status():
    """Returns dynamic engineering and safety status."""
    return build_status_payload()


@app.get("/telegram/status", dependencies=[Depends(verify_token)])
async def get_telegram_status():
    """Return Telegram bridge configuration without exposing secrets."""
    return {
        "enabled": bool(TELEGRAM_BOT_TOKEN and get_allowlist("telegram")),
        "bot_token_set": bool(TELEGRAM_BOT_TOKEN),
        "allowed_user_id_set": get_allowlist("telegram") is not None,
        "webhook_secret_set": bool(TELEGRAM_WEBHOOK_SECRET),
        "pending_count": len(load_telegram_pending()),
        "history_path": str(TELEGRAM_HISTORY_PATH),
    }


@app.get("/telegram/history", dependencies=[Depends(verify_token)])
async def get_telegram_history(limit: int = 25):
    """Return recent Telegram command history."""
    if not TELEGRAM_HISTORY_PATH.exists():
        return {"history": []}
    lines = await asyncio.to_thread(TELEGRAM_HISTORY_PATH.read_text, "utf-8")
    records = []
    for line in lines.splitlines()[-max(1, min(limit, 100)) :]:
        try:
            records.append(json.loads(line))
        except Exception as e:
            logger.warning("Skipping malformed telegram history line: %s", e)
    return {"history": records}


# ──────────────────────────────────────────────────────────────────────────────
# Priority Engine & Worker Matcher (extracted to aja/api/services/)
# ──────────────────────────────────────────────────────────────────────────────
# Compatibility re-exports: the scoring logic now lives in dedicated service
# modules; these imports keep the bridge namespace stable for route handlers.

from aja.api.services.priority_engine import (  # noqa: E402,F401
    CONSEQUENCE_MAP,
    DELEGATION_RULES,
    STAKEHOLDER_WEIGHTS,
    _days_until,
    _delegation_recommendation,
    _stakeholder_weight,
    _urgency_challenge,
    run_priority_engine,
)
from aja.api.services.worker_matcher import (  # noqa: E402,F401
    _extract_risk_level,
    _extract_speed_need,
    _infer_task_type,
    recommend_workers_for_task,
)



# ═══════════════════════════════════════════════════════════════════════════════
# Remote Baton Intake
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/baton/receive")
async def receive_baton_api(request: Request):
    """
    Receives a remote baton payload (Arrow-encoded mission state).

    Security: when AJA_BATON_SECRET is configured, the request MUST carry a
    valid HMAC-SHA256 signature of the raw body in the X-AJA-Signature header.
    Baton codes are strictly validated; payloads are size-capped.
    """
    raw = await request.body()
    if len(raw) > 16 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Baton payload too large")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from e

    from aja.runtime.handover import BatonManager

    signature = request.headers.get("X-AJA-Signature")
    try:
        code = await asyncio.to_thread(
            BatonManager().receive_baton, payload, signature, raw
        )
    except ValueError as e:
        logger.warning("Rejected incoming baton: %s", e)
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"ok": True, "code": code}


# ═══════════════════════════════════════════════════════════════════════════════
# Capabilities / Tools API
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/tools", dependencies=[Depends(verify_token)])
async def list_tools_api():
    """List all registered capabilities in the swarm."""
    from aja.capabilities import registry

    tools = []
    for name, cap in registry.capabilities.items():
        tools.append(
            {
                "name": name,
                "description": cap.__doc__ or "No description provided.",
                "parameters": getattr(cap, "schema", {}),
                "type": cap.__class__.__name__,
            }
        )
    return {"tools": tools, "total": len(tools)}


# ═══════════════════════════════════════════════════════════════════════════════
# Worker Registry API Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/workers", dependencies=[Depends(verify_token)])
async def list_workers_api(status: str | None = None, limit: int = 50):
    """List all registered workers, optionally filtered by availability status."""
    workers = await asyncio.to_thread(
        get_aja_memory().list_workers, status, min(limit, 100)
    )
    return {"workers": workers, "total": len(workers)}


@app.get("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def get_worker_api(worker_id: str):
    """Get a specific worker's profile."""
    worker = await asyncio.to_thread(get_aja_memory().get_worker, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
    return worker


@app.post("/workers", dependencies=[Depends(verify_token)])
async def create_worker_api(request: Request):
    """Register a new worker in the capability registry."""
    body = await request.json()
    try:
        worker = await asyncio.to_thread(get_aja_memory().create_worker, body)
        return {"ok": True, "worker": worker}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def update_worker_api(worker_id: str, request: Request):
    """Update an existing worker's profile."""
    body = await request.json()
    try:
        worker = await asyncio.to_thread(
            get_aja_memory().update_worker, worker_id, body
        )
        return {"ok": True, "worker": worker}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def delete_worker_api(worker_id: str):
    """Remove a worker from the registry."""
    deleted = await asyncio.to_thread(get_aja_memory().delete_worker, worker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
    return {"ok": True, "message": f"Worker {worker_id} deleted."}


@app.post("/workers/seed", dependencies=[Depends(verify_token)])
async def seed_workers_api():
    """Seed the worker registry with default profiles (idempotent)."""
    seeded = await asyncio.to_thread(get_aja_memory().seed_default_workers)
    return {"ok": True, "seeded": len(seeded), "workers": seeded}


@app.post("/workers/recommend", dependencies=[Depends(verify_token)])
async def recommend_workers_api(request: Request):
    """
    Get worker recommendations for a given task objective.
    AJA recommends — operator confirms.
    """
    body = await request.json()
    objective = body.get("objective", "").strip()
    if not objective:
        raise HTTPException(status_code=400, detail="Missing 'objective' field.")

    # Optional: fetch task context if task_id is provided
    task = None
    task_id = body.get("task_id")
    if task_id:
        task = await asyncio.to_thread(get_aja_memory().get_task, task_id)

    result = await asyncio.to_thread(
        recommend_workers_for_task,
        get_aja_memory(),
        objective,
        task,
        body.get("require_tests", False),
        body.get("require_git", False),
        body.get("require_deploy", False),
    )
    return result


@app.get("/workers/{worker_id}/history", dependencies=[Depends(verify_token)])
async def get_worker_history_api(worker_id: str, limit: int = 20):
    """Get execution history for a worker."""
    history = await asyncio.to_thread(
        get_aja_memory().get_worker_execution_history, worker_id, min(limit, 100)
    )
    return {"worker_id": worker_id, "history": history, "total": len(history)}


@app.post("/workers/{worker_id}/log", dependencies=[Depends(verify_token)])
async def log_worker_execution_api(worker_id: str, request: Request):
    """Log a worker execution outcome."""
    body = await request.json()
    body["worker_id"] = worker_id
    result = await asyncio.to_thread(get_aja_memory().log_worker_execution, body)
    return {"ok": True, "log": result}


@app.get("/priority/engine", dependencies=[Depends(verify_token)])
async def get_priority_engine():
    """
    Run the AJA Priority Engine across all active tasks.
    Returns top3, all_scored (descending priority_score), and ignore_candidates.
    """
    result = await asyncio.to_thread(run_priority_engine, get_aja_memory())
    return result


@app.get("/memory/tasks", dependencies=[Depends(verify_token)])
async def list_memory_tasks(
    status: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
):
    statuses = [item.strip().lower() for item in status.split(",")] if status else None
    tasks = await asyncio.to_thread(
        get_aja_memory().list_tasks, statuses, include_archived, limit
    )
    return {"tasks": tasks}


@app.post("/memory/tasks", dependencies=[Depends(verify_token)])
async def create_memory_task(request: Request):
    body = await request.json()
    body["source"] = body.get("source") or "dashboard"
    try:
        task = await asyncio.to_thread(get_aja_memory().create_task, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/memory/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def get_memory_task(task_id: str):
    task = await asyncio.to_thread(get_aja_memory().get_task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"task": task}


@app.patch("/memory/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def update_memory_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(get_aja_memory().update_task, task_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.post("/memory/tasks/{task_id}/complete", dependencies=[Depends(verify_token)])
async def complete_memory_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(
            get_aja_memory().complete_task, task_id, str(body.get("note") or "")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.post("/memory/tasks/{task_id}/archive", dependencies=[Depends(verify_token)])
async def archive_memory_task(task_id: str):
    try:
        task = await asyncio.to_thread(get_aja_memory().archive_task, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/memory/review", dependencies=[Depends(verify_token)])
async def review_memory_tasks(escalate: bool = True):
    review = await asyncio.to_thread(get_aja_memory().review, 7, 24, escalate)
    return {"review": review}


@app.get("/memory/summary", dependencies=[Depends(verify_token)])
async def memory_summary():
    summary = await asyncio.to_thread(get_aja_memory().summary)
    return {"summary": summary}


@app.get("/communications", dependencies=[Depends(verify_token)])
async def list_communications(
    delivery_status: str | None = None,
    approval_status: str | None = None,
    pending_follow_up: bool = False,
    limit: int = 50,
):
    messages = await asyncio.to_thread(
        get_aja_memory().list_communications,
        delivery_status,
        approval_status,
        pending_follow_up,
        limit,
    )
    return {"messages": messages}


@app.post("/communications", dependencies=[Depends(verify_token)])
async def create_communication(request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(get_aja_memory().create_communication, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.get("/communications/{message_id}", dependencies=[Depends(verify_token)])
async def get_communication(message_id: str):
    message = await asyncio.to_thread(get_aja_memory().get_communication, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    return {"message": message}


@app.patch("/communications/{message_id}", dependencies=[Depends(verify_token)])
async def update_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(
            get_aja_memory().update_communication, message_id, body
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/edit", dependencies=[Depends(verify_token)])
async def edit_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(
            get_aja_memory().edit_communication,
            message_id,
            str(body.get("draft_content") or ""),
            str(body.get("note") or "Edited from API."),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/approve", dependencies=[Depends(verify_token)])
async def approve_communication(message_id: str):
    try:
        message = await asyncio.to_thread(
            get_aja_memory().approve_communication, message_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/reject", dependencies=[Depends(verify_token)])
async def reject_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(
            get_aja_memory().reject_communication,
            message_id,
            str(body.get("reason") or ""),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/send", dependencies=[Depends(verify_token)])
async def send_communication(message_id: str):
    message = await asyncio.to_thread(get_aja_memory().get_communication, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    result = await send_communication_if_supported(message)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.get("/communications/summary/mobile", dependencies=[Depends(verify_token)])
async def communication_summary():
    summary = await asyncio.to_thread(get_aja_memory().communication_summary)
    return {"summary": summary}


@app.get("/scheduler/config", dependencies=[Depends(verify_token)])
async def get_scheduler_config():
    config = await asyncio.to_thread(get_aja_memory().get_scheduler_config)
    return {"config": config}


@app.patch("/scheduler/config", dependencies=[Depends(verify_token)])
async def update_scheduler_config(request: Request):
    body = await request.json()
    config = await asyncio.to_thread(get_aja_memory().update_scheduler_config, body)
    return {"ok": True, "config": config}


@app.get("/scheduler/review/{kind}", dependencies=[Depends(verify_token)])
async def get_scheduler_review(kind: str, escalate: bool = True):
    if kind not in {"morning", "night", "weekly"}:
        raise HTTPException(
            status_code=400, detail="Review kind must be morning, night, or weekly."
        )
    review = await asyncio.to_thread(
        get_aja_memory().generate_executive_review, kind, escalate
    )
    return {"review": review}


@app.post("/scheduler/review/{kind}/deliver", dependencies=[Depends(verify_token)])
async def deliver_scheduler_review(kind: str, request: Request):
    if kind not in {"morning", "night", "weekly"}:
        raise HTTPException(
            status_code=400, detail="Review kind must be morning, night, or weekly."
        )
    body = await request.json()
    result = await deliver_executive_review(
        kind, body.get("chat_id"), bool(body.get("force", False))
    )
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/scheduler/run", dependencies=[Depends(verify_token)])
async def run_scheduler_due_reviews(request: Request):
    body = await request.json()
    force = bool(body.get("force", False))
    chat_id = body.get("chat_id")
    kinds = (
        ["morning", "night", "weekly"]
        if force
        else await asyncio.to_thread(get_aja_memory().due_review_kinds)
    )
    results = []
    for kind in kinds:
        results.append(await deliver_executive_review(kind, chat_id, force=force))
    return {"ok": True, "results": results}


@app.post("/scheduler/snooze/{task_id}", dependencies=[Depends(verify_token)])
async def snooze_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(
            get_aja_memory().snooze_task,
            task_id,
            body.get("until") or "tomorrow",
            body.get("reason") or "Snoozed from API.",
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.post("/telegram/command", dependencies=[Depends(verify_token)])
async def post_telegram_command(request: Request):
    """Local test endpoint for the Telegram command router."""
    body = await request.json()
    user_id = int(body.get("user_id") or TELEGRAM_ALLOWED_USER_ID or 0)
    chat_id = body.get("chat_id") or user_id
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text.")
    if not is_user_authorized("telegram", user_id):
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "unauthorized",
            }
        )
        raise HTTPException(status_code=403, detail="Telegram user is not whitelisted.")
    reply = await execute_telegram_command(text, user_id, chat_id)
    return {"ok": True, "reply": reply}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
):
    """Telegram Bot API webhook entrypoint."""
    ensure_telegram_secret(x_telegram_bot_api_secret_token)
    update = await request.json()
    message = get_telegram_message(update)
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = sender.get("id")
    text = message.get("text")

    if not chat_id or not user_id:
        return {"ok": True, "ignored": "non-message update"}

    if not text:
        append_telegram_history(
            {"user_id": user_id, "chat_id": chat_id, "decision": "ignored_non_text"}
        )
        await send_telegram_message(
            chat_id, "Text commands only for now. Send /help for the allowlist."
        )
        return {"ok": True}

    if not is_user_authorized("telegram", user_id):
        append_telegram_history(
            {
                "user_id": user_id,
                "chat_id": chat_id,
                "command": text,
                "decision": "unauthorized",
            }
        )
        await send_telegram_message(
            chat_id, "Access denied: this Telegram user is not whitelisted for AJA."
        )
        return {"ok": True}

    reply = await execute_telegram_command(text, int(user_id), chat_id)
    await send_telegram_message(chat_id, reply)
    return {"ok": True}


@app.get("/diff", dependencies=[Depends(verify_token)])
def get_diff():
    try:
        diff = subprocess.check_output(
            ["git", "diff", "HEAD"], stderr=subprocess.STDOUT
        ).decode()
        if not diff.strip():
            return {
                "diff": "// All systems synchronized. No pending structural changes."
            }
        return {"diff": diff}
    except Exception:
        return {"diff": "// Unable to access structural history."}


@app.get("/git/history", dependencies=[Depends(verify_token)])
def get_git_history():
    try:
        output = subprocess.check_output(
            ["git", "log", "-n", "5", "--pretty=format:%h|%an|%ar|%s"],
            stderr=subprocess.STDOUT,
        ).decode()

        commits = []
        for line in output.split("\n"):
            if not line:
                continue
            h, an, ar, s = line.split("|")
            commits.append({"hash": h, "author": an, "time": ar, "subject": s})
        return {"commits": commits}
    except Exception:
        return {"commits": []}


@app.get("/runtime/approvals", dependencies=[Depends(verify_token)])
def get_pending_approval():
    state = load_runtime_state()
    return {"pending": state.get("pendingApproval")}


@app.get("/runtime/events", dependencies=[Depends(verify_token)])
def get_runtime_events():
    state = load_runtime_state()
    return {"events": state.get("events", [])[:10]}


@app.get("/runtime/batons", dependencies=[Depends(verify_token)])
def get_runtime_batons():
    return {"batons": load_baton_state()}


@app.get("/runtime/stream", dependencies=[Depends(verify_token)])
async def runtime_stream(request: Request):
    async def event_generator():
        last_payload = None

        while True:
            if await request.is_disconnected():
                break

            snapshot = await asyncio.to_thread(build_runtime_snapshot)
            payload = json.dumps(snapshot)

            if payload != last_payload:
                yield f"data: {payload}\n\n"
                last_payload = payload
            else:
                yield ": keepalive\n\n"

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/runtime/approve", dependencies=[Depends(verify_token)])
async def approve_pending():
    state = load_runtime_state()
    pending = state.get("pendingApproval")
    if not pending:
        raise HTTPException(status_code=404, detail="There is no pending approval.")
    if pending.get("requesterSource") == "Telegram":
        result = await approve_runtime_approval(pending.get("id"))
        if not result["ok"]:
            raise HTTPException(status_code=400, detail=result["message"])
        chat_id = (pending.get("telegram") or {}).get("chatId")
        if chat_id:
            await send_telegram_message(
                chat_id, f"Dashboard approved {pending.get('id')}.\n{result['message']}"
            )
        return result
    return await asyncio.to_thread(run_runtime_action, "approve")


@app.post("/runtime/deny", dependencies=[Depends(verify_token)])
async def deny_pending():
    state = load_runtime_state()
    pending = state.get("pendingApproval")
    if not pending:
        raise HTTPException(status_code=404, detail="There is no pending approval.")
    if pending.get("requesterSource") == "Telegram":
        result = reject_runtime_approval(pending.get("id"))
        chat_id = (pending.get("telegram") or {}).get("chatId")
        if chat_id:
            await send_telegram_message(
                chat_id, f"Dashboard rejected {pending.get('id')}."
            )
        return result
    return await asyncio.to_thread(run_runtime_action, "deny")


@app.get("/runtime/approvals/audit/{approval_id}", dependencies=[Depends(verify_token)])
async def get_approval_audit_trail(approval_id: str):
    """Return the append-only audit trail for a specific approval from aja_approval_audit."""
    trail = await asyncio.to_thread(get_aja_memory().list_approval_audit, approval_id)
    return {"approval_id": approval_id, "audit": trail}


@app.get("/runtime/events/db", dependencies=[Depends(verify_token)])
async def get_runtime_events_from_db(limit: int = 50):
    """Return recent runtime events from aja_runtime_events (authoritative LanceDB source)."""
    events = await asyncio.to_thread(
        get_aja_memory().get_runtime_events, min(limit, 200)
    )
    return {"events": events}


@app.post("/swarm/run", dependencies=[Depends(verify_token)])
async def swarm_run(request: Request):
    """Trigger a SwarmEngine mission from the dashboard."""
    body = await request.json()
    objective = body.get("objective", "").strip()
    if not objective:
        raise HTTPException(status_code=400, detail="Missing 'objective' field.")

    worker_id = body.get("worker_id", "swarm-maintenance")

    # Definition of Done — use provided list or auto-generate
    raw_dod = body.get("definition_of_done") or []
    if isinstance(raw_dod, str):
        raw_dod = [line.strip() for line in raw_dod.splitlines() if line.strip()]
    definition_of_done: list[str] = (
        raw_dod if raw_dod else generate_definition_of_done(objective)
    )

    # Write a delegation brief so BatonBoard can display DoD immediately
    brief_slug = objective[:40].replace(" ", "-").replace("/", "-").lower()
    brief_ts = int(time.time())
    brief_file = BATON_DIR / f"brief-{brief_ts}-{brief_slug}.json"
    brief_data = {
        "file": brief_file.name,
        "task": objective,
        "context": f"Delegated from Executive Desk at {now_iso()}",
        "status": "briefed",
        "stage": "pending_dispatch",
        "progress": 0,
        "definition_of_done": definition_of_done,
        "delegated_worker": worker_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    try:
        BATON_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            brief_file.write_text, json.dumps(brief_data, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to write delegation brief: {exc}"
        ) from exc

    try:
        proc = await asyncio.to_thread(
            subprocess.Popen,
            [
                sys.executable,
                "-m",
                "aja.orchestration.swarm",
                "--mode",
                "baton",
                "--objective",
                objective,
                "--worker",
                worker_id,
            ],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return {
            "ok": True,
            "message": f"Mission delegated to {worker_id}: {objective}",
            "pid": proc.pid,
            "definition_of_done": definition_of_done,
            "worker_id": worker_id,
            "brief_file": brief_file.name,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to launch SwarmEngine: {exc}"
        ) from exc


@app.get("/safety/pending", dependencies=[Depends(verify_token)])
def get_pending_legacy():
    state = load_runtime_state()
    pending = state.get("pendingApproval")
    return {"pending": [pending] if pending else []}


@app.get("/safety/history", dependencies=[Depends(verify_token)])
def get_safety_history():
    state = load_runtime_state()
    return {"events": state.get("events", [])[:10]}


CONFIG_PATH = _get_app_context().config_path


def load_config():
    return _load_config_store(CONFIG_PATH)


def save_config(data: dict):
    _save_config_store(data, CONFIG_PATH)


@app.get("/config", dependencies=[Depends(verify_token)])
def get_config():
    cfg = load_config()
    key = cfg.get("api_key", "")
    masked = mask_api_key(key)
    return {
        "provider": cfg.get("provider", "openrouter"),
        "api_key_masked": masked,
        "api_key_set": bool(key),
        "model": cfg.get("model", ""),
    }


@app.post("/config", dependencies=[Depends(verify_token)])
async def update_config(request: Request):
    body = await request.json()
    cfg = load_config()

    if "provider" in body:
        cfg["provider"] = body["provider"]
    if "api_key" in body and body["api_key"]:
        cfg["api_key"] = body["api_key"]
    if "model" in body:
        cfg["model"] = body["model"]

    save_config(cfg)
    return {"ok": True, "message": "Configuration saved."}


TRIGGER_RATE_LIMIT = 10
TRIGGER_WINDOW_S = 60.0
_trigger_counters: dict[str, list[float]] = {}


def check_trigger_rate_limit(source: str, now: float | None = None) -> bool:
    """Sliding-window rate limiter: max 10 triggers/minute per source."""
    now = time.time() if now is None else now
    window = _trigger_counters.setdefault(source, [])
    window[:] = [ts for ts in window if now - ts < TRIGGER_WINDOW_S]
    if len(window) >= TRIGGER_RATE_LIMIT:
        return False
    window.append(now)
    return True


@app.post("/api/v1/trigger", dependencies=[Depends(verify_token)])
async def trigger_mission(request: Request):
    """
    Accepts POST with JSON body {"goal": "...", "source": "..."}.
    Creates a mission immediately. Auth via existing Bearer token.
    Rate limited per source tag (max 10/minute).
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON body.")
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object.")

    goal = str(body.get("goal") or "").strip()
    if not goal:
        raise HTTPException(status_code=422, detail="Missing or empty 'goal' field.")

    client_host = request.client.host if request.client else "unknown"
    source = str(body.get("source") or client_host)
    if not check_trigger_rate_limit(source):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: max 10 triggers per minute per source.",
        )

    try:
        result = await asyncio.to_thread(get_aja_memory().create_mission, goal)
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"Failed to create mission: {exc}"
        ) from exc

    return JSONResponse(
        status_code=202,
        content={"mission_id": result.get("mission_id"), "status": "dispatched"},
    )


async def telegram_polling_loop():
    """Poll Telegram for updates with robust retry and error handling."""
    offset = 0
    consecutive_errors = 0
    print(f"[Telegram Poller] Loop active for bot: {TELEGRAM_BOT_TOKEN[:10]}...")
    while True:
        if not TELEGRAM_BOT_TOKEN:
            await asyncio.sleep(30)
            continue
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=30"

            def _fetch():
                try:
                    with urllib.request.urlopen(url, timeout=35) as response:
                        return json.loads(response.read().decode("utf-8"))
                except Exception as e:
                    return {"ok": False, "error": redact_secrets(str(e))}

            res = await asyncio.to_thread(_fetch)
            if res.get("ok"):
                consecutive_errors = 0
                updates = res.get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    message = get_telegram_message(update)
                    chat_id = (message.get("chat") or {}).get("id")
                    user_id = (message.get("from") or {}).get("id")
                    text = message.get("text")
                    if not chat_id or not user_id or not text:
                        continue
                    print(f"[Telegram Poller] Ingress: {user_id} > {text[:50]}...")
                    if not is_user_authorized("telegram", user_id):
                        print(f"[Telegram Poller] Blocked unauthorized user: {user_id}")
                        await send_telegram_message(
                            chat_id, "🔒 *Access Denied*: User is not whitelisted."
                        )
                        continue
                    try:
                        reply = await execute_telegram_command(
                            text, int(user_id), chat_id
                        )
                        await send_telegram_message(chat_id, reply)
                    except Exception as exec_err:
                        print(f"[Telegram Poller] Execution error: {redact_secrets(str(exec_err))}")
                        await send_telegram_message(
                            chat_id, f"❌ *Internal Error*: {exec_err}"
                        )
            else:
                error_msg = str(res.get("error", "Unknown"))
                if "409" in error_msg:
                    print(
                        "[Telegram Poller] Conflict (409): Another instance or webhook is active. Waiting 20s..."
                    )
                    await asyncio.sleep(20)
                elif "401" in error_msg:
                    print(
                        "[Telegram Poller] Unauthorized (401): Bot token is invalid. Hibernating..."
                    )
                    await asyncio.sleep(300)
                elif "timeout" in error_msg.lower():
                    pass
                else:
                    consecutive_errors += 1
                    wait_time = min(60, 2**consecutive_errors)
                    print(
                        f"[Telegram Poller] Fetch failed: {error_msg}. Retrying in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[Telegram Poller] Critical Loop Error: {redact_secrets(str(e))}")
            await asyncio.sleep(10)
        await asyncio.sleep(0.5)


def start_dashboard():
    """Compatibility shim for the deprecated dashboard launcher."""
    payload = dashboard_unavailable_payload()
    print(f"[!] {payload['message']} ({payload['path']})")
    return payload


if __name__ == "__main__":
    import uvicorn

    host, port = resolve_bridge_bind()
    uvicorn.run(app, host=host, port=port)
