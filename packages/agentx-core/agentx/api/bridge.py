import asyncio
import sys
import os
from pathlib import Path

# Add package root to sys.path to allow absolute imports when launched directly.
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.append(str(PACKAGE_ROOT))

import json
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agentx.security.stripper import CommandStripper
from agentx.config import PROJECT_ROOT
from agentx.memory.secretary import (
    SecretaryMemory,
    format_communication_for_mobile,
    format_tasks_for_mobile,
    parse_communication_intent,
    parse_task_intent,
)

app = FastAPI()

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

RUNTIME_STATE_PATH = Path(".agentx") / "runtime-state.json"  # debug export only
BATON_DIR = PROJECT_ROOT / "temp_batons"
API_TOKEN = os.getenv("AGENTX_API_TOKEN", "dev-token-123")
TELEGRAM_HISTORY_PATH = Path(".agentx") / "telegram-history.jsonl"
TELEGRAM_PENDING_PATH = Path(".agentx") / "telegram-pending.json"  # debug export only
APPROVAL_AUDIT_PATH = Path(".agentx") / "approval-audit.jsonl"   # debug export only
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_COMMAND_TIMEOUT = int(os.getenv("TELEGRAM_COMMAND_TIMEOUT", "60"))
SECRETARY_DB_PATH = Path(".agentx") / "aja_secretary.sqlite3"

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


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_telegram_history(event: dict):
    TELEGRAM_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"created_at": now_iso(), **event}
    with TELEGRAM_HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def append_approval_audit(event: dict):
    """Persist an approval audit entry to SQLite (authoritative) and JSONL (debug export)."""
    get_secretary_memory().log_approval_audit({
        "approval_id": event.get("id", "unknown"),
        "action": event.get("action", "unknown"),
        "requester_source": event.get("requester_source"),
        "command": event.get("command"),
        "risk_level": event.get("risk_level"),
        "reasons": event.get("reasons"),
        "exit_code": event.get("exit_code"),
        "note": event.get("note"),
    })
    # Debug export to JSONL (optional, non-authoritative)
    try:
        APPROVAL_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"created_at": now_iso(), **event}
        with APPROVAL_AUDIT_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    except Exception:
        pass


def save_runtime_state(state: dict):
    """Write a debug snapshot of runtime state to JSON. Not authoritative — SQLite is."""
    try:
        RUNTIME_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUNTIME_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def add_runtime_event(event: dict):
    """Append a runtime event to SQLite (authoritative source of truth)."""
    get_secretary_memory().add_runtime_event({
        "event_type": event.get("type", "INFO"),
        "tool": event.get("tool"),
        "message": event.get("message", ""),
        "command": event.get("command"),
        "root_binary": event.get("rootBinary"),
        "level": event.get("level"),
        "metadata": {k: v for k, v in event.items() if k not in {"type", "tool", "message", "command", "rootBinary", "level"}},
    })


def set_runtime_pending_approval(approval: dict | None):
    """Mark pending approval resolved (None) or write a new approval row to SQLite."""
    if approval is None:
        # Expire any remaining 'pending' rows (belt-and-suspenders)
        mem = get_secretary_memory()
        active = mem.get_active_approval()
        if active:
            mem.update_approval(active["approval_id"], "resolved", "Cleared by system.")
    else:
        # The canonical write happens in create_approval_in_db; this is a no-op guard.
        pass


def create_approval_in_db(approval: dict) -> str:
    """Persist a new approval object to SQLite and return the approval_id."""
    return get_secretary_memory().create_approval({
        "approval_id": approval.get("id"),
        "tool": approval.get("tool", "bash"),
        "command": approval.get("command"),
        "command_preview": approval.get("commandPreview") or approval.get("command"),
        "action_type": approval.get("actionType"),
        "root_binary": approval.get("rootBinary"),
        "risk_level": approval.get("riskLevel", "medium"),
        "level": approval.get("level"),
        "reasons": approval.get("reasons", []),
        "human_reason": approval.get("humanReason"),
        "rollback_path": approval.get("rollbackPath"),
        "dry_run_summary": approval.get("dryRunSummary"),
        "requester_source": approval.get("requesterSource", "CLI"),
        "telegram_meta": approval.get("telegram") or {},
        "expires_at": approval.get("expiresAt"),
    })


def load_telegram_pending():
    """Returns active pending approvals keyed by approval_id (read from SQLite)."""
    active = get_secretary_memory().get_active_approval()
    if not active:
        return {}
    return {active["approval_id"]: active}


def save_telegram_pending(data: dict):
    """No-op: Telegram approvals now live in aja_approvals table."""
    # Debug export only
    try:
        TELEGRAM_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        TELEGRAM_PENDING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


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


def get_secretary_memory():
    return SecretaryMemory(PROJECT_ROOT / SECRETARY_DB_PATH)


def format_status_for_mobile(payload: dict):
    territories = payload.get("territories", [])
    territory_lines = [
        f"- {item.get('name')}: {item.get('status')} ({item.get('load')})"
        for item in territories[:5]
    ]
    pending = payload.get("pending_approval")
    lines = [
        "AJA status",
        f"Files: {payload.get('total_files', 0)}",
        f"Active agents: {payload.get('active_agents', 0)}",
        f"Batons: {payload.get('baton_count', 0)}",
        f"Safety alerts: {payload.get('safety_alerts', 0)}",
    ]
    if territory_lines:
        lines.append("Territories:")
        lines.extend(territory_lines)
    if pending:
        lines.append(f"Pending approval: {pending.get('tool', 'unknown')}")
    return "\n".join(lines)


def build_secretary_help():
    return "\n".join(
        [
            "AJA secretary memory",
            "Commands:",
            "- tasks",
            "- task review",
            "- complete <task_id>",
            "- archive <task_id>",
            "- draft recruiter follow-up",
            "- draft professional reply to recruiter",
            "- remind Rahul about project deadline",
            "- approve message <message_id>",
            "- send message <message_id>",
            "- check pending unanswered messages",
            "- add task <title> due <date> priority <low|medium|high|urgent>",
            "",
            "Examples:",
            "- remind me if I skip gym every day",
            "- follow up with recruiter next Tuesday",
            "- internship application status check due next Tuesday",
            "- bill payment reminder due tomorrow priority high",
        ]
    )


def generate_definition_of_done(objective: str) -> list[str]:
    """
    Auto-generate a Definition of Done from an objective string.
    AJA refuses to delegate without clear success criteria.
    """
    o = objective.lower()
    items: list[str] = ["Goal achieved as described in the brief", "Handoff summary or output note provided"]

    if any(k in o for k in ("code", "build", "implement", "create", "write", "develop", "scaffold", "generate")):
        items += ["Code reviewed and clean", "Unit tests pass", "No secret or key leakage"]

    if any(k in o for k in ("auth", "login", "token", "session", "security", "password", "credential")):
        items += ["Authentication works end-to-end", "Rollback path documented", "No credentials hardcoded"]

    if any(k in o for k in ("fix", "debug", "resolve", "patch", "repair", "bug")):
        items += ["Root cause identified and documented", "Fix verified with test", "No regressions introduced"]

    if any(k in o for k in ("refactor", "clean", "restructure", "reorganize")):
        items += ["Behaviour unchanged (no regressions)", "Readability improved", "PR summary generated"]

    if any(k in o for k in ("test", "verify", "validate", "check", "qa")):
        items += ["All cases pass (happy path + edge cases)", "Results documented"]

    if any(k in o for k in ("deploy", "release", "publish", "ship", "launch")):
        items += ["Deployment verified in target environment", "Health checks pass", "Rollback plan documented"]

    if any(k in o for k in ("email", "message", "reply", "send", "draft", "notify")):
        items += ["Message content reviewed and approved", "Recipient confirmed", "Tone appropriate for context"]

    if any(k in o for k in ("research", "find", "analyze", "analyse", "report", "investigate")):
        items += ["Findings documented with sources", "Conclusions are actionable", "Gaps / unknowns flagged"]

    if any(k in o for k in ("apply", "application", "resume", "cv", "recruiter", "interview", "job")):
        items += ["Application submitted and confirmation received", "Follow-up reminder set", "Status logged in secretary memory"]

    if any(k in o for k in ("payment", "pay", "bill", "invoice", "transfer")):
        items += ["Transaction confirmed", "Receipt logged", "Amount verified"]

    if any(k in o for k in ("pr", "pull request", "merge")):
        items += ["PR description complete", "Review comments addressed", "Merge approved by owner"]

    # Always append PR summary for engineering tasks
    if any(k in o for k in ("code", "build", "implement", "fix", "debug", "deploy", "refactor")):
        items.append("PR summary or handoff note generated")

    # Deduplicate preserving order
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


async def send_communication_if_supported(message: dict):
    if message["approval_status"] != "approved":
        return {"ok": False, "message": "Message is not approved yet."}
    if message["channel"] != "telegram":
        return {
            "ok": False,
            "message": "No direct send adapter is configured for this channel. The approved draft remains ready for manual sending.",
        }

    result = await send_telegram_message(message["recipient"], message["draft_content"])
    if not result.get("ok"):
        return {"ok": False, "message": f"Telegram send failed: {result.get('description', 'unknown error')}"}
    sent = get_secretary_memory().mark_communication_sent(message["message_id"], "Sent through Telegram Bot API.")
    return {"ok": True, "message": f"Sent Telegram message {sent['message_id']} to {sent['recipient']}."}


async def deliver_executive_review(kind: str, chat_id: int | str | None = None, force: bool = False):
    memory = get_secretary_memory()
    if not force and kind not in memory.due_review_kinds():
        return {"ok": False, "message": f"{kind} review is not due."}
    review = await asyncio.to_thread(memory.generate_executive_review, kind, True)
    target_chat = chat_id or os.getenv("TELEGRAM_REVIEW_CHAT_ID") or TELEGRAM_ALLOWED_USER_ID
    if not target_chat:
        return {"ok": False, "message": "No Telegram review chat is configured."}
    result = await send_telegram_message(target_chat, review["summary"])
    if not result.get("ok"):
        return {"ok": False, "message": f"Telegram delivery failed: {result.get('description', 'unknown error')}"}
    event = await asyncio.to_thread(
        memory.record_scheduler_event,
        f"{kind}_review",
        str(target_chat),
        {"summary": review["summary"]},
        True,
    )
    return {"ok": True, "review": review, "event": event}


def execute_secretary_command_sync(text: str, source: str, owner: str = "AJA"):
    normalized = " ".join((text or "").strip().split())
    lowered = normalized.lower()
    memory = get_secretary_memory()

    if lowered in {"tasks", "task summary", "secretary summary", "memory summary"}:
        return memory.summary()

    if lowered in {"task help", "secretary help", "memory help"}:
        return build_secretary_help()

    if lowered in {"task review", "review tasks", "secretary review"}:
        review = memory.review(escalate=True)
        tasks = memory.list_tasks(statuses=["pending", "active", "blocked"], limit=10)
        return format_tasks_for_mobile(tasks, review)

    if lowered in {"morning review", "daily morning review"}:
        return memory.generate_executive_review("morning", escalate=True)["summary"]

    if lowered in {"night review", "daily night review"}:
        return memory.generate_executive_review("night", escalate=True)["summary"]

    if lowered in {"weekly review", "what slipped this week"}:
        return memory.generate_executive_review("weekly", escalate=True)["summary"]

    if lowered in {"what am i avoiding today", "what am i avoiding"}:
        return memory.generate_executive_review("morning", escalate=True)["summary"]

    if lowered.startswith("why is ") and "still pending" in lowered:
        return memory.generate_executive_review("morning", escalate=True)["summary"]

    # ── Priority Engine Telegram Commands ──────────────────────────────────────
    if lowered in {
        "what should i do first",
        "what should i do",
        "priorities",
        "top priorities",
        "what's most important",
        "whats most important",
    }:
        result = run_priority_engine(memory)
        top3 = result["top3"]
        if not top3:
            return "No active tasks found. You're clear."
        lines = ["Top priorities right now:\n"]
        for i, item in enumerate(top3, 1):
            rec = item.get("decision_recommendation", "Review")
            tier = item.get("urgency_tier", "")
            lines.append(f"{i}. {item['title']}")
            lines.append(f"   → {rec}")
            if tier:
                lines.append(f"   Tier: {tier}")
            lines.append("")
        return "\n".join(lines).strip()

    if lowered in {
        "what actually matters today",
        "what matters today",
        "what's important today",
        "whats important today",
        "today's priorities",
        "todays priorities",
    }:
        result = run_priority_engine(memory)
        focus = [t for t in result["top3"] if t.get("urgency_tier") in ("critical", "high")]
        ignore = result.get("ignore_candidates", [])
        lines = []
        if focus:
            lines.append("What actually matters today:\n")
            for item in focus:
                lines.append(f"• {item['title']}")
                lines.append(f"  → {item.get('decision_recommendation','')}")
                if item.get("urgency_challenge"):
                    lines.append(f"  Note: {item['urgency_challenge']}")
                lines.append("")
        else:
            lines.append("Nothing truly critical today. Consider working on medium-priority items.")
        if ignore:
            lines.append(f"\nSafe to defer: {', '.join(t['title'] for t in ignore[:3])}")
        return "\n".join(lines).strip()

    if lowered in {
        "what can be ignored this week",
        "what can i ignore this week",
        "what can i skip this week",
        "what can wait this week",
        "low priority this week",
    }:
        result = run_priority_engine(memory)
        ignore = result.get("ignore_candidates", [])
        if not ignore:
            return "Nothing can safely be ignored this week — all tasks have meaningful priority scores."
        lines = ["Safe to defer or archive this week:\n"]
        for item in ignore:
            reason = item.get("ignore_reason", "Low urgency and low consequence of delay.")
            lines.append(f"• {item['title']}")
            lines.append(f"  Reason: {reason}")
            lines.append("")
        lines.append("AJA will remind you if anything escalates.")
        return "\n".join(lines).strip()

    if lowered.startswith("snooze "):
        parts = normalized.split(maxsplit=2)
        if len(parts) < 2:
            return "Use: snooze <task_id> [until]"
        task_id = parts[1]
        until = parts[2] if len(parts) > 2 else "tomorrow"
        try:
            task = memory.snooze_task(task_id, until, "Snoozed from command.")
            return f"Snoozed: {task['title']}\nuntil: {(task.get('reminder_state') or {}).get('snoozed_until')}"
        except KeyError:
            return f"No secretary task found for {task_id}."

    for prefix, action in (("complete ", "complete"), ("done ", "complete"), ("archive ", "archive")):
        if lowered.startswith(prefix):
            task_id = normalized.split(maxsplit=1)[1].strip()
            try:
                task = memory.complete_task(task_id) if action == "complete" else memory.archive_task(task_id)
                return f"{action.title()}d: {task['title']}\nid: {task['task_id']}\nstatus: {task['status']}"
            except KeyError:
                return f"No secretary task found for {task_id}."

    if lowered in {"communications", "communication summary", "drafts", "message drafts", "check pending unanswered messages"}:
        return memory.communication_summary()

    if lowered.startswith("approve message "):
        message_id = normalized.split(maxsplit=2)[2].strip()
        try:
            message = memory.approve_communication(message_id)
            return f"Approved message {message['message_id']}. It is ready to send, but not sent yet."
        except KeyError:
            return f"No message found for {message_id}."

    if lowered.startswith("reject message "):
        message_id = normalized.split(maxsplit=2)[2].strip()
        try:
            message = memory.reject_communication(message_id)
            return f"Rejected message {message['message_id']}."
        except KeyError:
            return f"No message found for {message_id}."

    if lowered.startswith("edit message "):
        parts = normalized.split(maxsplit=3)
        if len(parts) < 4:
            return "Use: edit message <message_id> <new text>"
        try:
            message = memory.edit_communication(parts[2], parts[3], "Edited from command.")
            return format_communication_for_mobile(message)
        except KeyError:
            return f"No message found for {parts[2]}."

    task_data = parse_task_intent(normalized, source=source, owner=owner)
    if task_data:
        task = memory.create_task(task_data)
        due = task.get("due_date") or "no due date"
        return "\n".join(
            [
                "Saved secretary task",
                f"ID: {task['task_id']}",
                f"Title: {task['title']}",
                f"Priority: {task['priority']}",
                f"Due: {due}",
                f"Status: {task['status']}",
            ]
        )

    message_data = parse_communication_intent(normalized, source=source)
    if message_data:
        message = memory.create_communication(message_data)
        return format_communication_for_mobile(message)

    return None


def seconds_until_tonight(hour=23, minute=30):
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def build_supported_command(text: str):
    normalized = " ".join((text or "").strip().lower().split())
    if normalized in {"/start", "help", "/help"}:
        return {
            "kind": "help",
            "message": "\n".join(
                [
                    "AJA Telegram control is online.",
                    "Commands:",
                    "- status",
                    "- check gpu",
                    "- run training job",
                    "- git pull repo",
                    "- shutdown laptop tonight",
                    "- restart notebook process",
                    "- tasks",
                    "- task review",
                    "- complete <task_id>",
                    "",
                    "You can also talk naturally. AJA will answer as a chat assistant.",
                    "Risky commands create structured approval requests.",
                    "Use approve <id> or reject <id> after reviewing the request.",
                ]
            ),
        }
    if normalized == "status":
        return {"kind": "status"}
    if normalized in {"check gpu", "gpu status", "gpu", "/gpu", "check_gpu"}:
        return {"kind": "execute", "command": "nvidia-smi", "requires_confirmation": False, "action_type": "gpu_check", "risk_level": "low"}
    if normalized == "run training job":
        return {
            "kind": "execute",
            "command": f'"{sys.executable}" agentx.py run --bg "run training job"',
            "requires_confirmation": True,
            "reason": "Starts a background AJA mission powered by AgentX Core.",
            "action_type": "training_job",
            "risk_level": "medium",
        }
    if normalized == "git pull repo":
        return {
            "kind": "execute",
            "command": "git pull --ff-only",
            "requires_confirmation": True,
            "reason": "Updates the repository working tree.",
            "action_type": "git_update",
            "risk_level": "medium",
        }
    if normalized == "shutdown laptop tonight":
        delay = seconds_until_tonight()
        return {
            "kind": "execute",
            "command": f'shutdown /s /t {delay} /c "Scheduled by AJA Telegram"',
            "requires_confirmation": True,
            "reason": f"Schedules Windows shutdown in about {delay // 60} minutes.",
            "action_type": "scheduled_shutdown",
            "risk_level": "high",
        }
    if normalized == "restart notebook process":
        return {
            "kind": "execute",
            "command": 'powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name jupyter-notebook,jupyter-lab -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Process jupyter-notebook"',
            "requires_confirmation": True,
            "reason": "Stops known Jupyter notebook processes and starts a new notebook process.",
            "action_type": "notebook_restart",
            "risk_level": "high",
        }
    return {
        "kind": "chat",
    }


def analyze_shell_command(command: str):
    stripper = CommandStripper(command)
    stripper.strip()
    analysis = stripper.report()
    root = (analysis.get("Root Binary") or "").lower()
    reasons = []

    if root in DENY_BINARIES:
        reasons.append(DENY_BINARIES[root])
    for pattern in analysis.get("Dangerous Patterns", []):
        if pattern in DENY_PATTERNS:
            reasons.append(DENY_PATTERNS[pattern])
    if analysis.get("Blocked Env Vars"):
        reasons.append(
            "Blocked environment variables detected: "
            + ", ".join(analysis.get("Blocked Env Vars", {}).keys())
            + "."
        )
    if reasons:
        return {"decision": "deny", "level": "CRITICAL", "reasons": reasons, "analysis": analysis}

    ask_reasons = []
    if root in ASK_BINARIES:
        ask_reasons.append(ASK_BINARIES[root])
    for pattern in analysis.get("Dangerous Patterns", []):
        if pattern in ASK_PATTERNS:
            ask_reasons.append(ASK_PATTERNS[pattern])
    if analysis.get("Operators"):
        ask_reasons.append("Compound shell operators require explicit confirmation.")
    if ask_reasons:
        return {"decision": "ask", "level": "HIGH" if root in {"shutdown", "taskkill"} else "MEDIUM", "reasons": ask_reasons, "analysis": analysis}

    return {"decision": "allow", "level": "LOW", "reasons": [], "analysis": analysis}


def normalize_risk_level(level: str):
    normalized = (level or "").lower()
    if normalized in {"critical", "high"}:
        return "high"
    if normalized == "medium":
        return "medium"
    return "low"


def build_rollback_path(action_type: str, command: str):
    lowered = command.lower()
    if "shutdown /s" in lowered:
        return "Run: shutdown /a before the timer expires."
    if lowered.startswith("git pull"):
        return "Use git reflog to find the previous HEAD, then reset only after reviewing local changes."
    if "jupyter" in lowered or "notebook" in lowered:
        return "Stop the restarted notebook process and relaunch the previous notebook command if needed."
    if action_type == "training_job":
        return "Stop the spawned background process from Task Manager or terminal logs; workspace files are not changed directly by the launcher."
    return "No automatic rollback is known. Review output and restore from version control or backup if needed."


def build_dry_run_summary(action_type: str, command: str):
    if action_type == "git_update":
        return "Would fetch and fast-forward the current repository only if Git can do so without a merge commit."
    if action_type == "scheduled_shutdown":
        return "Would schedule a Windows shutdown timer. It can be canceled before expiry with shutdown /a."
    if action_type == "notebook_restart":
        return "Would stop known Jupyter notebook/lab processes, then start a new notebook process."
    if action_type == "training_job":
        return "Would delegate a background AJA training mission through AgentX Core."
    if action_type == "gpu_check":
        return "Would query NVIDIA GPU status with nvidia-smi."
    return f"Would execute: {command}"


def build_approval_object(text: str, command: str, spec: dict, classification: dict, user_id: int, chat_id: int | str):
    action_type = spec.get("action_type", "shell_command")
    reasons = [spec.get("reason")] if spec.get("reason") else []
    reasons.extend(classification.get("reasons", []))
    risk_level = normalize_risk_level(classification.get("level", "MEDIUM"))
    if spec.get("risk_level"):
        risk_level = spec["risk_level"]
    request_id = f"approval-{int(time.time())}-{abs(hash((user_id, command, time.time()))) % 10000}"
    expires_at = (datetime.now().astimezone() + timedelta(minutes=10)).isoformat(timespec="seconds")
    analysis = classification.get("analysis") or {}
    return {
        "id": request_id,
        "tool": "bash",
        "input": {"command": command},
        "command": command,
        "commandPreview": command,
        "actionType": action_type,
        "rootBinary": analysis.get("Root Binary"),
        "level": classification.get("level", "MEDIUM"),
        "riskLevel": risk_level,
        "reasons": [reason for reason in reasons if reason],
        "humanReason": spec.get("reason") or (reasons[0] if reasons else "This action needs human review before execution."),
        "rollbackPath": build_rollback_path(action_type, command),
        "expiresAt": expires_at,
        "requesterSource": "Telegram",
        "dryRunSummary": build_dry_run_summary(action_type, command),
        "createdAt": now_iso(),
        "telegram": {"userId": user_id, "chatId": chat_id, "text": text},
    }


def format_approval_for_mobile(approval: dict):
    reasons = approval.get("reasons") or []
    reason_text = "\n".join(f"- {reason}" for reason in reasons) or "- Manual review required."
    return "\n".join(
        [
            "Approval request",
            f"ID: {approval.get('id')}",
            f"Action: {approval.get('actionType')}",
            f"Risk: {approval.get('riskLevel', approval.get('level', 'medium'))}",
            f"Source: {approval.get('requesterSource')}",
            f"Expires: {approval.get('expiresAt')}",
            "",
            "Command:",
            approval.get("commandPreview") or approval.get("command") or "(unknown)",
            "",
            "Reason:",
            approval.get("humanReason") or "Review required.",
            "",
            "Expected effect:",
            approval.get("dryRunSummary") or "No dry-run summary available.",
            "",
            "Rollback:",
            approval.get("rollbackPath") or "No rollback path known.",
            "",
            "Review notes:",
            reason_text,
            "",
            f"Approve: approve {approval.get('id')}",
            f"Reject: reject {approval.get('id')}",
        ]
    )


async def run_shell_command(command: str):
    def _run():
        return subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            shell=True,
            capture_output=True,
            text=True,
            timeout=TELEGRAM_COMMAND_TIMEOUT,
        )

    result = await asyncio.to_thread(_run)
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\nErrors:\n{result.stderr.strip()}".strip()
    return {
        "ok": result.returncode == 0,
        "code": result.returncode,
        "output": compact_text(output),
    }


async def run_file_guardian_check(command: str):
    def _run():
        return subprocess.run(
            [resolve_npx_executable(), "tsx", "apps/cli-ts/src/telegram_file_guardian_check.ts", ".agentx/telegram-command.txt", command],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )

    result = await asyncio.to_thread(_run)
    try:
        payload = json.loads((result.stdout or result.stderr).strip())
    except Exception:
        payload = {"decision": "DENY", "error": (result.stderr or result.stdout).strip()}

    decision = str(payload.get("decision", "DENY")).upper()
    if result.returncode != 0 and decision != "DENY":
        decision = "DENY"
    return {"decision": decision, "error": payload.get("error")}


def get_pending_approval_by_id(request_id: str):
    """Look up an approval by ID from SQLite (single source of truth)."""
    row = get_secretary_memory().get_approval(request_id)
    if row and row.get("status") == "pending":
        return row
    return None


def approval_is_expired(approval: dict):
    expires_at = approval.get("expiresAt") or approval.get("expires_at")
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(str(expires_at).replace("Z", "+00:00")).timestamp() <= time.time()
    except Exception:
        return True


async def approve_runtime_approval(request_id: str, user_id: int | None = None):
    approval = get_pending_approval_by_id(request_id)
    if not approval:
        return {"ok": False, "message": "No pending approval found for that id."}
    if user_id is not None:
        telegram_meta = approval.get("telegram_meta") or {}
        telegram_user = telegram_meta.get("userId") or telegram_meta.get("userId") or approval.get("user_id")
        if telegram_user is not None and int(telegram_user) != int(user_id):
            return {"ok": False, "message": "That approval belongs to a different Telegram user."}
    if approval_is_expired(approval):
        mem = get_secretary_memory()
        mem.update_approval(request_id, "expired", "Expired without action.")
        mem.log_approval_audit({"approval_id": request_id, "action": "expired",
                                "requester_source": approval.get("requester_source"),
                                "command": approval.get("command")})
        return {"ok": False, "message": "Approval expired. Send the command again."}

    command = approval.get("command")
    if not command:
        return {"ok": False, "message": "Approval has no executable command."}

    file_guardian = await run_file_guardian_check(command)
    classification = analyze_shell_command(command)
    if file_guardian["decision"] == "DENY" or classification["decision"] == "deny":
        mem = get_secretary_memory()
        mem.update_approval(request_id, "blocked", "Blocked at execution re-check.")
        reasons = classification.get("reasons", [])
        if file_guardian.get("error"):
            reasons.append(file_guardian["error"])
        mem.log_approval_audit({"approval_id": request_id, "action": "blocked_at_execution",
                                "command": command, "reasons": reasons})
        return {"ok": False, "message": "Approval blocked at execution re-check: " + "; ".join(reasons or ["FileGuardian denied the command."])}

    mem = get_secretary_memory()
    mem.log_approval_audit({"approval_id": request_id, "action": "approved",
                            "requester_source": approval.get("requester_source"), "command": command})
    result = await run_shell_command(command)
    mem.update_approval(request_id, "resolved" if result["ok"] else "failed",
                        compact_text(result["output"], 300))
    mem.add_runtime_event({
        "event_type": "APPROVED" if result["ok"] else "DENY",
        "tool": approval.get("tool", "bash"),
        "message": compact_text(result["output"], 500),
        "command": command,
        "root_binary": approval.get("root_binary"),
        "level": approval.get("level"),
    })
    mem.log_approval_audit({"approval_id": request_id,
                            "action": "executed" if result["ok"] else "execution_failed",
                            "exit_code": result["code"], "command": command})
    prefix = "OK" if result["ok"] else f"Failed ({result['code']})"
    return {"ok": result["ok"], "message": f"{prefix}: {approval.get('action_type', 'action')}\n{result['output']}"}


def reject_runtime_approval(request_id: str, user_id: int | None = None):
    approval = get_pending_approval_by_id(request_id)
    if not approval:
        return {"ok": False, "message": "No pending approval found for that id."}
    if user_id is not None:
        telegram_meta = approval.get("telegram_meta") or {}
        telegram_user = telegram_meta.get("userId") or approval.get("user_id")
        if telegram_user is not None and int(telegram_user) != int(user_id):
            return {"ok": False, "message": "That approval belongs to a different Telegram user."}

    mem = get_secretary_memory()
    mem.update_approval(request_id, "rejected", "Rejected by operator.")
    mem.log_approval_audit({"approval_id": request_id, "action": "rejected",
                            "requester_source": approval.get("requester_source"),
                            "command": approval.get("command")})
    mem.add_runtime_event({
        "event_type": "DENIED",
        "tool": approval.get("tool", "bash"),
        "message": f"Rejected approval {request_id}.",
        "command": approval.get("command"),
        "root_binary": approval.get("root_binary"),
        "level": approval.get("level"),
    })
    return {"ok": True, "message": f"Rejected approval {request_id}."}


async def send_telegram_message(chat_id: int | str, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN is not configured."}

    def _send():
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        body = urllib.parse.urlencode(
            {
                "chat_id": str(chat_id),
                "text": compact_text(text, 3900),
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return await asyncio.to_thread(_send)
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def ensure_telegram_secret(secret_header: str | None):
    if TELEGRAM_WEBHOOK_SECRET and secret_header != TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid Telegram webhook secret.")


def get_telegram_message(update: dict):
    return update.get("message") or update.get("edited_message") or {}


def build_aja_chat_context(limit: int = 5):
    memory = get_secretary_memory()
    context: list[str] = []
    try:
        tasks = memory.list_tasks(statuses=["pending", "active", "blocked"], limit=limit)
        if tasks:
            context.append("Current tasks:")
            for task in tasks:
                due = task.get("due_at") or task.get("due_date") or "no due date"
                context.append(f"- {task.get('title')} [{task.get('priority')}, due {due}]")
    except Exception:
        pass

    try:
        pending = memory.get_active_approval()
        if pending:
            context.append(
                f"Pending approval: {pending.get('approval_id')} for {pending.get('command_preview') or pending.get('command')}"
            )
    except Exception:
        pass

    return "\n".join(context) or "No current task context is available."


def generate_aja_chat_reply(text: str, user_id: int, chat_id: int | str):
    system_prompt = (
        "You are AJA, the natural-language assistant/operator for AgentX Core. "
        "Be warm, concise, and practical, like a capable collaborator in Telegram. "
        "Do not claim you executed commands unless the safety-gated command path did so. "
        "When the user asks for risky system action, explain that they should use an explicit supported command "
        "or the dashboard so AgentX can create an approval request. "
        "For reminders, follow-ups, tasks, and communication drafts, mention that you can save them when phrased as a task or draft request."
    )
    user_prompt = (
        f"Telegram user id: {user_id}\n"
        f"Chat id: {chat_id}\n\n"
        f"AJA context:\n{build_aja_chat_context()}\n\n"
        f"User message:\n{text}\n\n"
        "Reply naturally for Telegram. Keep it under 900 characters unless detail is necessary."
    )

    try:
        from agentx.llm import completion

        reply = (completion(user_prompt, system_prompt=system_prompt) or "").strip()
        if reply:
            return compact_text(reply, 1800)
    except Exception as exc:
        return (
            "I can chat naturally, but my language model backend is not reachable right now. "
            f"Bridge error: {exc}\n\n"
            "I can still handle command paths like status, tasks, task review, approve <id>, and reject <id>."
        )

    return (
        "I am here. Natural chat is wired through the AJA bridge, but the model returned an empty response. "
        "Check your provider key or local model server, then message me again."
    )


async def execute_telegram_command(text: str, user_id: int, chat_id: int | str):
    pending = load_telegram_pending()
    normalized = " ".join((text or "").strip().split())
    lower = normalized.lower()

    if lower.startswith("approve message ") or lower.startswith("reject message "):
        secretary_reply = await asyncio.to_thread(execute_secretary_command_sync, text, "Telegram", f"telegram:{user_id}")
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "communication_approval"})
        return secretary_reply or "Unable to update message approval."

    if lower.startswith("reject ") or lower.startswith("deny "):
        request_id = normalized.split(maxsplit=1)[1].strip()
        result = reject_runtime_approval(request_id, user_id)
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "approval_rejected", "approval_id": request_id})
        return result["message"]

    if lower.startswith("approve ") or lower.startswith("confirm "):
        request_id = normalized.split(maxsplit=1)[1].strip()
        result = await approve_runtime_approval(request_id, user_id)
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "approval_approved" if result["ok"] else "approval_failed", "approval_id": request_id})
        return result["message"]

    if lower.startswith("send message "):
        message_id = normalized.split(maxsplit=2)[2].strip()
        message = await asyncio.to_thread(get_secretary_memory().get_communication, message_id)
        if not message:
            return f"No message found for {message_id}."
        result = await send_communication_if_supported(message)
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "communication_send" if result["ok"] else "communication_send_failed", "message_id": message_id})
        return result["message"]

    spec = build_supported_command(text)
    if spec["kind"] == "help":
        return spec["message"]
    if spec["kind"] == "status":
        payload = await asyncio.to_thread(build_status_payload)
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "status"})
        return format_status_for_mobile(payload)

    # Only if it's not a hardcoded command or status/help, try the secretary
    if spec["kind"] == "chat":
        secretary_reply = await asyncio.to_thread(execute_secretary_command_sync, text, "Telegram", f"telegram:{user_id}")
        if secretary_reply:
            append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "secretary_memory"})
            return secretary_reply

        # Fallback to general AI chat
        reply = await asyncio.to_thread(generate_aja_chat_reply, text, user_id, chat_id)
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "aja_chat"})
        return reply

    # If we are here, it's an 'execute' kind from build_supported_command
    command = spec["command"]
    file_guardian = await run_file_guardian_check(command)
    if file_guardian["decision"] == "DENY":
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "mapped_command": command, "decision": "blocked_by_file_guardian", "error": file_guardian.get("error")})
        detail = f": {file_guardian['error']}" if file_guardian.get("error") else "."
        return f"Command denied by FileGuardian{detail}"

    classification = analyze_shell_command(command)
    if classification["decision"] == "deny":
        reason_text = "\n".join(f"- {reason}" for reason in classification["reasons"])
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "mapped_command": command, "decision": "blocked", "reasons": classification["reasons"]})
        return f"Command denied by AJA Safety Gate.\n{reason_text}"

    if spec.get("requires_confirmation") or classification["decision"] == "ask" or file_guardian["decision"] == "ASK":
        if file_guardian["decision"] == "ASK":
            classification["reasons"].append("FileGuardian requested review before execution.")
        approval = build_approval_object(text, command, spec, classification, user_id, chat_id)
        # --- AJA Brain: persist approval to SQLite (single source of truth) ---
        create_approval_in_db(approval)
        mem = get_secretary_memory()
        mem.add_runtime_event({
            "event_type": "ASK",
            "tool": "bash",
            "message": approval["humanReason"],
            "command": command,
            "root_binary": approval.get("rootBinary"),
            "level": approval.get("level"),
        })
        mem.log_approval_audit({
            "approval_id": approval["id"],
            "action": "requested",
            "requester_source": "Telegram",
            "command": command,
            "risk_level": approval["riskLevel"],
        })
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "mapped_command": command, "decision": "approval_requested", "approval_id": approval["id"], "reasons": approval["reasons"]})
        return format_approval_for_mobile(approval)

    result = await run_shell_command(command)
    append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "mapped_command": command, "decision": "executed", "exit_code": result["code"]})
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
        raise HTTPException(status_code=500, detail=f"Failed to launch runtime action: {exc}") from exc

    payload_raw = (result.stdout or result.stderr).strip()
    try:
        payload = json.loads(payload_raw) if payload_raw else {"ok": result.returncode == 0, "message": ""}
    except json.JSONDecodeError:
        payload = {"ok": result.returncode == 0, "message": payload_raw}

    if result.returncode != 0:
      raise HTTPException(status_code=500, detail=payload.get("message") or "Runtime action failed.")

    return payload


def load_runtime_state():
    """Build a legacy-compatible runtime state dict from SQLite (AJA Brain)."""
    mem = get_secretary_memory()
    pending_row = mem.get_active_approval()
    events = mem.get_runtime_events(50)
    # Convert DB rows to the legacy shape the dashboard and snapshot builder expect
    pending = None
    if pending_row:
        pending = {
            "id": pending_row.get("approval_id"),
            "tool": pending_row.get("tool"),
            "command": pending_row.get("command"),
            "commandPreview": pending_row.get("command_preview") or pending_row.get("command"),
            "actionType": pending_row.get("action_type"),
            "rootBinary": pending_row.get("root_binary"),
            "riskLevel": pending_row.get("risk_level"),
            "level": pending_row.get("level"),
            "reasons": pending_row.get("reasons", []),
            "humanReason": pending_row.get("human_reason"),
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
        "total_files": sum(len(files) for _, _, files in os.walk("src")) if Path("src").exists() else 0,
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


@app.get("/status")
def get_status():
    """Returns dynamic engineering and safety status."""
    return build_status_payload()


@app.get("/telegram/status", dependencies=[Depends(verify_token)])
async def get_telegram_status():
    """Return Telegram bridge configuration without exposing secrets."""
    return {
        "enabled": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_ID),
        "bot_token_set": bool(TELEGRAM_BOT_TOKEN),
        "allowed_user_id_set": bool(TELEGRAM_ALLOWED_USER_ID),
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
        except Exception:
            pass
    return {"history": records}


# ──────────────────────────────────────────────────────────────────────────────
# Priority Engine — multi-factor executive scoring
# ──────────────────────────────────────────────────────────────────────────────

STAKEHOLDER_WEIGHTS: dict[str, int] = {
    "recruiter": 90,
    "hiring manager": 95,
    "client": 85,
    "employer": 85,
    "manager": 80,
    "friend": 40,
    "personal": 30,
    "system": 20,
    "maintenance": 15,
    "default": 35,
}

CONSEQUENCE_MAP: dict[str, int] = {
    "urgent": 35,
    "high": 25,
    "medium": 15,
    "low": 5,
}

DELEGATION_RULES = {
    # Keywords in task title / description that suggest who should handle it
    "code": "Delegate to AgentX Worker",
    "debug": "Delegate to AgentX Worker",
    "fix bug": "Delegate to AgentX Worker",
    "refactor": "Delegate to AgentX Worker",
    "test": "Delegate to AgentX Worker",
    "deploy": "Ask user first",
    "send": "Ask user first",
    "approve": "Ask user first",
    "email": "Ask user first",
    "reply": "Ask user first",
    "call": "Ask user first",
    "review": "Ask user first",
    "apply": "Do now — time-sensitive",
    "submit": "Do now — time-sensitive",
    "payment": "Do now — financial risk",
    "bill": "Do now — financial risk",
    "deadline": "Do now — deadline miss risk",
    "interview": "Do now — opportunity cost",
    "offer": "Do now — opportunity cost",
}


def _days_until(due_date_str: str | None) -> float | None:
    """Return signed days until due_date_str. Negative means overdue."""
    if not due_date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            due = datetime.strptime(due_date_str[:19], fmt[:len(due_date_str[:19])])
            return (due - datetime.now()).total_seconds() / 86400
        except ValueError:
            continue
    return None


def _stakeholder_weight(task: dict) -> int:
    title_lower = (task.get("title") or "").lower()
    desc_lower = (task.get("description") or "").lower()
    combined = f"{title_lower} {desc_lower}"
    for keyword, weight in sorted(STAKEHOLDER_WEIGHTS.items(), key=lambda x: -x[1]):
        if keyword in combined:
            return weight
    return STAKEHOLDER_WEIGHTS["default"]


def _delegation_recommendation(task: dict) -> str:
    title_lower = (task.get("title") or "").lower()
    desc_lower = (task.get("description") or "").lower()
    combined = f"{title_lower} {desc_lower}"
    for keyword, rec in DELEGATION_RULES.items():
        if keyword in combined:
            return rec
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent", "high"):
        return "Do now"
    if priority == "medium":
        return "Follow up tomorrow"
    return "Archive — no real consequence"


def _urgency_challenge(days_left: float | None, priority: str) -> str | None:
    """Return a challenge message if the task's urgency may be inflated."""
    if days_left is None:
        return None
    if days_left > 5 and priority in ("low", "medium"):
        return "This feels urgent, but nothing breaks if it waits until tomorrow."
    if days_left > 14 and priority == "high":
        return "Deadline is 2+ weeks away. Safe to plan rather than act immediately."
    return None


def run_priority_engine(memory: "SecretaryMemory") -> dict:
    """
    Score all active tasks using 5 dimensions:
      1. Urgency          — deadline proximity, overdue state, escalation age
      2. Stakeholder Weight — recruiter > client > friend > system
      3. Consequence      — financial, trust, opportunity, deadline risk
      4. Executive Intent — explicitly high-priority, repeated commitments
      5. Delegatability   — AJA / AgentX Worker / human

    Returns:
        top3        — top 3 tasks with full scoring metadata
        all_scored  — all tasks scored and sorted
        ignore_candidates — tasks safe to defer / archive this week
    """
    tasks = memory.list_tasks(
        statuses=["pending", "active", "blocked", "escalated"],
        include_archived=False,
        limit=100,
    )

    scored = []
    for task in tasks:
        priority = (task.get("priority") or "medium").lower()
        urgency_raw = task.get("urgency_score") or 0
        escalation = task.get("escalation_level") or 0
        days_left = _days_until(task.get("due_date"))
        stakeholder_w = _stakeholder_weight(task)
        consequence_w = CONSEQUENCE_MAP.get(priority, 15)

        # ── 1. Urgency score (0-40) ─────────────────────────────────────────
        urgency_pts = 0
        if days_left is not None:
            if days_left < 0:          # overdue
                urgency_pts = 40
            elif days_left < 1:        # due today
                urgency_pts = 35
            elif days_left < 3:        # due very soon
                urgency_pts = 25
            elif days_left < 7:
                urgency_pts = 15
            else:
                urgency_pts = max(0, 10 - int(days_left / 3))
        else:
            # No due date — rely on raw urgency_score
            urgency_pts = min(40, int(urgency_raw * 0.4))

        # Escalation age bonus
        urgency_pts = min(40, urgency_pts + escalation * 5)

        # ── 2. Stakeholder weight (0-30) ────────────────────────────────────
        stakeholder_pts = int(stakeholder_w * 0.3)  # scale 0-95 → 0-28

        # ── 3. Consequence of delay (0-20) ──────────────────────────────────
        consequence_pts = min(20, consequence_w)

        # ── 4. Executive Intent bonus (0-10) ────────────────────────────────
        intent_pts = 0
        if priority == "urgent":
            intent_pts = 10
        elif priority == "high":
            intent_pts = 6
        elif escalation >= 2:
            intent_pts = 8

        # ── Composite priority_score (0-100) ────────────────────────────────
        priority_score = min(100, urgency_pts + stakeholder_pts + consequence_pts + intent_pts)

        # ── Urgency tier ────────────────────────────────────────────────────
        if priority_score >= 80:
            tier = "critical"
        elif priority_score >= 60:
            tier = "high"
        elif priority_score >= 35:
            tier = "medium"
        else:
            tier = "low"

        # ── Delegation recommendation ────────────────────────────────────────
        delegation_rec = _delegation_recommendation(task)

        # ── Escalation recommendation ────────────────────────────────────────
        should_escalate = escalation < 2 and (days_left is not None and days_left < 0 or priority_score >= 80)
        escalation_rec = "Escalate now — overdue or critical" if should_escalate else (
            "Monitor" if priority_score >= 50 else "No escalation needed"
        )

        # ── Ignore / Archive suggestion ─────────────────────────────────────
        can_ignore = priority_score < 25 and (days_left is None or days_left > 7)
        ignore_reason = None
        if can_ignore:
            if days_left and days_left > 14:
                ignore_reason = f"Due in {int(days_left)} days — low urgency, no stakeholder risk."
            else:
                ignore_reason = "Low priority, no deadline pressure, no stakeholder consequence."

        # ── Approval recommendation ──────────────────────────────────────────
        approval_rec = (
            "Approve immediately" if priority_score >= 80
            else "Review before acting" if priority_score >= 50
            else "No approval needed — low risk"
        )

        # ── Urgency challenge ────────────────────────────────────────────────
        challenge = _urgency_challenge(days_left, priority)

        scored.append({
            **task,
            "priority_score": priority_score,
            "urgency_tier": tier,
            "urgency_pts": urgency_pts,
            "stakeholder_pts": stakeholder_pts,
            "consequence_pts": consequence_pts,
            "intent_pts": intent_pts,
            "decision_recommendation": delegation_rec,
            "escalation_recommendation": escalation_rec,
            "can_ignore": can_ignore,
            "ignore_reason": ignore_reason,
            "approval_recommendation": approval_rec,
            "urgency_challenge": challenge,
            "days_until_due": round(days_left, 1) if days_left is not None else None,
        })

    scored.sort(key=lambda t: -t["priority_score"])
    top3 = scored[:3]
    ignore_candidates = [t for t in scored if t["can_ignore"]]

    return {
        "top3": top3,
        "all_scored": scored,
        "ignore_candidates": ignore_candidates,
        "total_tasks": len(scored),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6.1 — Worker Recommendation Engine
# ═══════════════════════════════════════════════════════════════════════════════

_TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "code": ["code", "implement", "build", "create", "feature", "develop", "write code", "add"],
    "fix": ["fix", "bug", "error", "broken", "issue", "debug", "patch", "repair", "crash"],
    "refactor": ["refactor", "restructure", "clean", "reorganize", "simplify", "modularize"],
    "test": ["test", "spec", "coverage", "unit test", "e2e", "integration test", "assert", "tdd"],
    "deploy": ["deploy", "release", "ci/cd", "pipeline", "production", "staging", "publish"],
    "review": ["review", "audit", "inspect", "check", "evaluate", "pr review"],
    "research": ["research", "investigate", "analyze", "explore", "study", "compare", "evaluate"],
    "documentation": ["document", "readme", "docs", "wiki", "explain", "api doc", "specification"],
    "git": ["git", "commit", "branch", "merge", "rebase", "cherry-pick", "stash"],
    "pr": ["pull request", "pr", "merge request"],
    "maintenance": ["maintain", "health", "monitor", "clean up", "optimize", "repair"],
    "analysis": ["analyze", "analyse", "report", "metric", "dashboard", "data"],
}

_SPEED_MAP = {"fast": 3, "medium": 2, "slow": 1}


def _infer_task_type(objective: str) -> list[str]:
    """Infer task type tags from a free-text objective."""
    objective_lower = objective.lower()
    matched: list[str] = []
    for task_type, keywords in _TASK_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in objective_lower:
                matched.append(task_type)
                break
    return matched or ["code"]  # default to 'code' if nothing matched


def _extract_risk_level(task: dict | None) -> str:
    """Determine the risk level from a task's priority or explicit risk field."""
    if not task:
        return "medium"
    risk = (task.get("approval_risk_level") or "").lower()
    if risk in ("high", "critical"):
        return "high"
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent", "high"):
        return "high"
    if priority == "low":
        return "low"
    return "medium"


def _extract_speed_need(task: dict | None) -> str:
    """Determine how fast we need the worker to be."""
    if not task:
        return "medium"
    days_left = _days_until((task or {}).get("due_date"))
    if days_left is not None and days_left < 1:
        return "fast"
    if days_left is not None and days_left < 3:
        return "fast"
    priority = (task.get("priority") or "medium").lower()
    if priority in ("urgent",):
        return "fast"
    return "medium"


def recommend_workers_for_task(
    memory: "SecretaryMemory",
    objective: str,
    task: dict | None = None,
    require_tests: bool = False,
    require_git: bool = False,
    require_deploy: bool = False,
) -> dict:
    """
    Score and rank all available workers against a task objective.

    Returns:
        recommended  — ranked list of worker recommendations with scores + reasons
        analysis     — task analysis summary
        cautions     — any warnings for the human decision-maker
    """
    workers = memory.list_workers(status="available", limit=50)
    if not workers:
        all_workers = memory.list_workers(limit=50)
        return {
            "recommended": [],
            "analysis": {
                "objective": objective,
                "inferred_types": _infer_task_type(objective),
                "risk_level": _extract_risk_level(task),
                "speed_need": _extract_speed_need(task),
            },
            "cautions": [
                f"No available workers found. {len(all_workers)} worker(s) registered but none with availability_status='available'."
            ],
            "total_workers": len(all_workers),
        }

    inferred_types = _infer_task_type(objective)
    risk_level = _extract_risk_level(task)
    speed_need = _extract_speed_need(task)

    recommendations = []
    for worker in workers:
        score = 0.0
        reasons: list[str] = []
        cautions: list[str] = []

        # ── 1. Task type match (0-30) ─────────────────────────────────────────
        preferred = set(worker.get("preferred_task_types") or [])
        blocked = set(worker.get("blocked_task_types") or [])
        matched_types = set(inferred_types) & preferred
        blocked_types = set(inferred_types) & blocked

        if blocked_types:
            cautions.append(f"Blocked for: {', '.join(blocked_types)}")
            score -= 50  # heavy penalty, but keep in list for transparency
        if matched_types:
            type_score = min(30, len(matched_types) * 10)
            score += type_score
            reasons.append(f"Matches task types: {', '.join(matched_types)}")
        elif not blocked_types:
            score += 5
            reasons.append("General-purpose worker (no specific type match)")

        # ── 2. Capability requirements (0-15) ────────────────────────────────
        cap_score = 0
        if require_tests and worker.get("supports_tests"):
            cap_score += 5
            reasons.append("Supports test execution")
        elif require_tests and not worker.get("supports_tests"):
            cautions.append("Does NOT support tests (required)")
            score -= 10
        if require_git and worker.get("supports_git_operations"):
            cap_score += 5
            reasons.append("Supports git operations")
        elif require_git and not worker.get("supports_git_operations"):
            cautions.append("Does NOT support git operations (required)")
            score -= 10
        if require_deploy and worker.get("supports_deployment"):
            cap_score += 5
            reasons.append("Supports deployment")
        elif require_deploy and not worker.get("supports_deployment"):
            cautions.append("Does NOT support deployment (required)")
            score -= 15
        score += cap_score

        # ── 3. Reliability (0-25) ────────────────────────────────────────────
        reliability = worker.get("reliability_score", 0.5)
        reliability_pts = reliability * 25
        score += reliability_pts
        if reliability >= 0.9:
            reasons.append(f"High reliability ({reliability:.0%})")
        elif reliability < 0.7:
            cautions.append(f"Low reliability ({reliability:.0%})")

        # ── 4. Speed match (0-15) ────────────────────────────────────────────
        worker_speed = _SPEED_MAP.get(worker.get("execution_speed", "medium"), 2)
        needed_speed = _SPEED_MAP.get(speed_need, 2)
        if worker_speed >= needed_speed:
            speed_pts = min(15, worker_speed * 5)
            score += speed_pts
            if speed_need == "fast" and worker_speed >= 3:
                reasons.append("Fast execution — matches urgency")
        else:
            score += max(0, worker_speed * 3)
            if speed_need == "fast":
                cautions.append("Slower than ideal for this urgent task")

        # ── 5. Cost profile (0-10) ───────────────────────────────────────────
        cost = worker.get("cost_profile", "subscription")
        if cost == "free":
            score += 10
            reasons.append("Zero-cost execution")
        elif cost == "subscription":
            score += 7
            reasons.append("Subscription-based (no per-task cost)")
        else:
            score += 3
            cautions.append("Pay-per-use — may incur additional costs")

        # ── 6. Risk alignment (0-5) ──────────────────────────────────────────
        worker_risk = worker.get("approval_risk_level", "medium")
        if risk_level == "high" and worker_risk == "low":
            score += 5
            reasons.append("Low-risk worker for high-risk task — safer choice")
        elif risk_level == "low":
            score += 3

        # ── 7. Historical performance bonus (0-10) ──────────────────────────
        success_rate = worker.get("historical_success_rate", 0)
        total_executed = worker.get("total_tasks_executed", 0)
        if total_executed >= 5:
            perf_pts = min(10, success_rate / 10)
            score += perf_pts
            if success_rate >= 90:
                reasons.append(f"Proven track record ({success_rate:.0f}% success over {total_executed} tasks)")
            elif success_rate < 60:
                cautions.append(f"Low success rate ({success_rate:.0f}%)")

        # ── 8. Strength keyword overlap (0-5) bonus ──────────────────────────
        strengths = " ".join(worker.get("primary_strengths") or []).lower()
        objective_lower = objective.lower()
        strength_hits = sum(1 for word in objective_lower.split() if word in strengths and len(word) > 3)
        if strength_hits > 0:
            score += min(5, strength_hits * 2)
            reasons.append(f"Strength keywords overlap ({strength_hits} matches)")

        # Final composite
        final_score = max(0, min(100, score))

        recommendations.append({
            "worker_id": worker["worker_id"],
            "worker_name": worker["worker_name"],
            "worker_type": worker["worker_type"],
            "recommendation_score": round(final_score, 1),
            "reasons": reasons,
            "cautions": cautions,
            "execution_speed": worker.get("execution_speed"),
            "reliability_score": worker.get("reliability_score"),
            "cost_profile": worker.get("cost_profile"),
            "supports_tests": worker.get("supports_tests"),
            "supports_git": worker.get("supports_git_operations"),
            "supports_deploy": worker.get("supports_deployment"),
            "recent_failures": (worker.get("recent_failures") or [])[:3],
        })

    recommendations.sort(key=lambda r: -r["recommendation_score"])

    # Top recommendation advisory
    top = recommendations[0] if recommendations else None
    advisory: list[str] = []
    if top and top["cautions"]:
        advisory.append(f"Top pick ({top['worker_name']}) has cautions: {'; '.join(top['cautions'])}")
    if len(recommendations) >= 2:
        gap = recommendations[0]["recommendation_score"] - recommendations[1]["recommendation_score"]
        if gap < 5:
            advisory.append(
                f"Close match between {recommendations[0]['worker_name']} and {recommendations[1]['worker_name']} "
                f"(gap: {gap:.1f}pts) — your judgment matters here."
            )

    return {
        "recommended": recommendations,
        "analysis": {
            "objective": objective,
            "inferred_types": inferred_types,
            "risk_level": risk_level,
            "speed_need": speed_need,
            "require_tests": require_tests,
            "require_git": require_git,
            "require_deploy": require_deploy,
        },
        "cautions": advisory,
        "total_workers": len(workers),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Worker Registry API Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/workers", dependencies=[Depends(verify_token)])
async def list_workers_api(status: str | None = None, limit: int = 50):
    """List all registered workers, optionally filtered by availability status."""
    workers = await asyncio.to_thread(get_secretary_memory().list_workers, status, min(limit, 100))
    return {"workers": workers, "total": len(workers)}


@app.get("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def get_worker_api(worker_id: str):
    """Get a specific worker's profile."""
    worker = await asyncio.to_thread(get_secretary_memory().get_worker, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
    return worker


@app.post("/workers", dependencies=[Depends(verify_token)])
async def create_worker_api(request: Request):
    """Register a new worker in the capability registry."""
    body = await request.json()
    try:
        worker = await asyncio.to_thread(get_secretary_memory().create_worker, body)
        return {"ok": True, "worker": worker}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def update_worker_api(worker_id: str, request: Request):
    """Update an existing worker's profile."""
    body = await request.json()
    try:
        worker = await asyncio.to_thread(get_secretary_memory().update_worker, worker_id, body)
        return {"ok": True, "worker": worker}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/workers/{worker_id}", dependencies=[Depends(verify_token)])
async def delete_worker_api(worker_id: str):
    """Remove a worker from the registry."""
    deleted = await asyncio.to_thread(get_secretary_memory().delete_worker, worker_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
    return {"ok": True, "message": f"Worker {worker_id} deleted."}


@app.post("/workers/seed", dependencies=[Depends(verify_token)])
async def seed_workers_api():
    """Seed the worker registry with default profiles (idempotent)."""
    seeded = await asyncio.to_thread(get_secretary_memory().seed_default_workers)
    return {"ok": True, "seeded": len(seeded), "workers": seeded}


@app.post("/workers/recommend", dependencies=[Depends(verify_token)])
async def recommend_workers_api(request: Request):
    """
    Get worker recommendations for a given task objective.
    AJA recommends — human confirms.
    """
    body = await request.json()
    objective = body.get("objective", "").strip()
    if not objective:
        raise HTTPException(status_code=400, detail="Missing 'objective' field.")

    # Optional: fetch task context if task_id is provided
    task = None
    task_id = body.get("task_id")
    if task_id:
        task = await asyncio.to_thread(get_secretary_memory().get_task, task_id)

    result = await asyncio.to_thread(
        recommend_workers_for_task,
        get_secretary_memory(),
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
    history = await asyncio.to_thread(get_secretary_memory().get_worker_execution_history, worker_id, min(limit, 100))
    return {"worker_id": worker_id, "history": history, "total": len(history)}


@app.post("/workers/{worker_id}/log", dependencies=[Depends(verify_token)])
async def log_worker_execution_api(worker_id: str, request: Request):
    """Log a worker execution outcome."""
    body = await request.json()
    body["worker_id"] = worker_id
    result = await asyncio.to_thread(get_secretary_memory().log_worker_execution, body)
    return {"ok": True, "log": result}


@app.get("/priority/engine", dependencies=[Depends(verify_token)])
async def get_priority_engine():
    """
    Run the AJA Priority Engine across all active tasks.
    Returns top3, all_scored (descending priority_score), and ignore_candidates.
    """
    result = await asyncio.to_thread(run_priority_engine, get_secretary_memory())
    return result


@app.get("/memory/tasks", dependencies=[Depends(verify_token)])
async def list_memory_tasks(
    status: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
):
    statuses = [item.strip().lower() for item in status.split(",")] if status else None
    tasks = await asyncio.to_thread(get_secretary_memory().list_tasks, statuses, include_archived, limit)
    return {"tasks": tasks}


@app.post("/memory/tasks", dependencies=[Depends(verify_token)])
async def create_memory_task(request: Request):
    body = await request.json()
    body["source"] = body.get("source") or "dashboard"
    try:
        task = await asyncio.to_thread(get_secretary_memory().create_task, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/memory/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def get_memory_task(task_id: str):
    task = await asyncio.to_thread(get_secretary_memory().get_task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"task": task}


@app.patch("/memory/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def update_memory_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(get_secretary_memory().update_task, task_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.post("/memory/tasks/{task_id}/complete", dependencies=[Depends(verify_token)])
async def complete_memory_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(get_secretary_memory().complete_task, task_id, str(body.get("note") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.post("/memory/tasks/{task_id}/archive", dependencies=[Depends(verify_token)])
async def archive_memory_task(task_id: str):
    try:
        task = await asyncio.to_thread(get_secretary_memory().archive_task, task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/memory/review", dependencies=[Depends(verify_token)])
async def review_memory_tasks(escalate: bool = True):
    review = await asyncio.to_thread(get_secretary_memory().review, 7, 24, escalate)
    return {"review": review}


@app.get("/memory/summary", dependencies=[Depends(verify_token)])
async def memory_summary():
    summary = await asyncio.to_thread(get_secretary_memory().summary)
    return {"summary": summary}


@app.get("/communications", dependencies=[Depends(verify_token)])
async def list_communications(
    delivery_status: str | None = None,
    approval_status: str | None = None,
    pending_follow_up: bool = False,
    limit: int = 50,
):
    messages = await asyncio.to_thread(
        get_secretary_memory().list_communications,
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
        message = await asyncio.to_thread(get_secretary_memory().create_communication, body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.get("/communications/{message_id}", dependencies=[Depends(verify_token)])
async def get_communication(message_id: str):
    message = await asyncio.to_thread(get_secretary_memory().get_communication, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    return {"message": message}


@app.patch("/communications/{message_id}", dependencies=[Depends(verify_token)])
async def update_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(get_secretary_memory().update_communication, message_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/edit", dependencies=[Depends(verify_token)])
async def edit_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(
            get_secretary_memory().edit_communication,
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
        message = await asyncio.to_thread(get_secretary_memory().approve_communication, message_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/reject", dependencies=[Depends(verify_token)])
async def reject_communication(message_id: str, request: Request):
    body = await request.json()
    try:
        message = await asyncio.to_thread(get_secretary_memory().reject_communication, message_id, str(body.get("reason") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True, "message": message}


@app.post("/communications/{message_id}/send", dependencies=[Depends(verify_token)])
async def send_communication(message_id: str):
    message = await asyncio.to_thread(get_secretary_memory().get_communication, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Message not found.")
    result = await send_communication_if_supported(message)
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.get("/communications/summary/mobile", dependencies=[Depends(verify_token)])
async def communication_summary():
    summary = await asyncio.to_thread(get_secretary_memory().communication_summary)
    return {"summary": summary}


@app.get("/scheduler/config", dependencies=[Depends(verify_token)])
async def get_scheduler_config():
    config = await asyncio.to_thread(get_secretary_memory().get_scheduler_config)
    return {"config": config}


@app.patch("/scheduler/config", dependencies=[Depends(verify_token)])
async def update_scheduler_config(request: Request):
    body = await request.json()
    config = await asyncio.to_thread(get_secretary_memory().update_scheduler_config, body)
    return {"ok": True, "config": config}


@app.get("/scheduler/review/{kind}", dependencies=[Depends(verify_token)])
async def get_scheduler_review(kind: str, escalate: bool = True):
    if kind not in {"morning", "night", "weekly"}:
        raise HTTPException(status_code=400, detail="Review kind must be morning, night, or weekly.")
    review = await asyncio.to_thread(get_secretary_memory().generate_executive_review, kind, escalate)
    return {"review": review}


@app.post("/scheduler/review/{kind}/deliver", dependencies=[Depends(verify_token)])
async def deliver_scheduler_review(kind: str, request: Request):
    if kind not in {"morning", "night", "weekly"}:
        raise HTTPException(status_code=400, detail="Review kind must be morning, night, or weekly.")
    body = await request.json()
    result = await deliver_executive_review(kind, body.get("chat_id"), bool(body.get("force", False)))
    if not result["ok"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/scheduler/run", dependencies=[Depends(verify_token)])
async def run_scheduler_due_reviews(request: Request):
    body = await request.json()
    force = bool(body.get("force", False))
    chat_id = body.get("chat_id")
    kinds = ["morning", "night", "weekly"] if force else await asyncio.to_thread(get_secretary_memory().due_review_kinds)
    results = []
    for kind in kinds:
        results.append(await deliver_executive_review(kind, chat_id, force=force))
    return {"ok": True, "results": results}


@app.post("/scheduler/snooze/{task_id}", dependencies=[Depends(verify_token)])
async def snooze_task(task_id: str, request: Request):
    body = await request.json()
    try:
        task = await asyncio.to_thread(
            get_secretary_memory().snooze_task,
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
    if not TELEGRAM_ALLOWED_USER_ID or str(user_id) != str(TELEGRAM_ALLOWED_USER_ID):
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "unauthorized"})
        raise HTTPException(status_code=403, detail="Telegram user is not whitelisted.")
    reply = await execute_telegram_command(text, user_id, chat_id)
    return {"ok": True, "reply": reply}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
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
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "decision": "ignored_non_text"})
        await send_telegram_message(chat_id, "Text commands only for now. Send /help for the allowlist.")
        return {"ok": True}

    if not TELEGRAM_ALLOWED_USER_ID or str(user_id) != str(TELEGRAM_ALLOWED_USER_ID):
        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "unauthorized"})
        await send_telegram_message(chat_id, "Access denied: this Telegram user is not whitelisted for AJA.")
        return {"ok": True}

    reply = await execute_telegram_command(text, int(user_id), chat_id)
    await send_telegram_message(chat_id, reply)
    return {"ok": True}


@app.get("/diff")
def get_diff():
    try:
        diff = subprocess.check_output(["git", "diff", "HEAD"], stderr=subprocess.STDOUT).decode()
        if not diff.strip():
            return {"diff": "// All systems synchronized. No pending structural changes."}
        return {"diff": diff}
    except Exception:
        return {"diff": "// Unable to access structural history."}


@app.get("/git/history")
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


@app.get("/runtime/approvals")
def get_pending_approval():
    state = load_runtime_state()
    return {"pending": state.get("pendingApproval")}


@app.get("/runtime/events")
def get_runtime_events():
    state = load_runtime_state()
    return {"events": state.get("events", [])[:10]}


@app.get("/runtime/batons")
def get_runtime_batons():
    return {"batons": load_baton_state()}


@app.get("/runtime/stream")
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
            await send_telegram_message(chat_id, f"Dashboard approved {pending.get('id')}.\n{result['message']}")
        return result
    return run_runtime_action("approve")


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
            await send_telegram_message(chat_id, f"Dashboard rejected {pending.get('id')}.")
        return result
    return run_runtime_action("deny")


@app.get("/runtime/approvals/audit/{approval_id}", dependencies=[Depends(verify_token)])
async def get_approval_audit_trail(approval_id: str):
    """Return the append-only audit trail for a specific approval from aja_approval_audit."""
    trail = await asyncio.to_thread(get_secretary_memory().list_approval_audit, approval_id)
    return {"approval_id": approval_id, "audit": trail}


@app.get("/runtime/events/db", dependencies=[Depends(verify_token)])
async def get_runtime_events_from_db(limit: int = 50):
    """Return recent runtime events from aja_runtime_events (authoritative SQLite source)."""
    events = await asyncio.to_thread(get_secretary_memory().get_runtime_events, min(limit, 200))
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
    definition_of_done: list[str] = raw_dod if raw_dod else generate_definition_of_done(objective)

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
        brief_file.write_text(json.dumps(brief_data, indent=2), encoding="utf-8")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write delegation brief: {exc}") from exc

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "agentx.orchestration.swarm", "--mode", "baton", "--objective", objective, "--worker", worker_id],
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
        raise HTTPException(status_code=500, detail=f"Failed to launch SwarmEngine: {exc}") from exc


@app.get("/safety/pending")
def get_pending_legacy():
    state = load_runtime_state()
    pending = state.get("pendingApproval")
    return {"pending": [pending] if pending else []}


@app.get("/safety/history")
def get_safety_history():
    state = load_runtime_state()
    return {"events": state.get("events", [])[:10]}


CONFIG_PATH = Path(".agentx") / "config.json"


def load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"provider": "openrouter", "api_key": "", "model": ""}


def save_config(data: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


@app.get("/config")
def get_config():
    cfg = load_config()
    # Mask the API key for safety — only show last 4 chars
    key = cfg.get("api_key", "")
    masked = ("*" * max(0, len(key) - 4)) + key[-4:] if len(key) > 4 else key
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


async def telegram_polling_loop():
    """Poll Telegram for updates when running locally without a webhook."""
    offset = 0
    print(f"[Telegram Poller] Starting polling loop for bot token: {TELEGRAM_BOT_TOKEN[:10]}...")
    
    while True:
        if not TELEGRAM_BOT_TOKEN:
            await asyncio.sleep(10)
            continue
            
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={offset}&timeout=20"
            def _fetch():
                try:
                    with urllib.request.urlopen(url, timeout=25) as response:
                        return json.loads(response.read().decode("utf-8"))
                except Exception as e:
                    return {"ok": False, "error": str(e)}

            res = await asyncio.to_thread(_fetch)
            if res.get("ok"):
                updates = res.get("result", [])
                if updates:
                    print(f"[Telegram Poller] Received {len(updates)} new updates.")
                
                for update in updates:
                    offset = update["update_id"] + 1
                    message = get_telegram_message(update)
                    chat = message.get("chat") or {}
                    sender = message.get("from") or {}
                    chat_id = chat.get("id")
                    user_id = sender.get("id")
                    text = message.get("text")

                    if not chat_id or not user_id or not text:
                        continue

                    print(f"[Telegram Poller] Processing message from {user_id}: {text}")

                    # Process update exactly like the webhook does
                    if not TELEGRAM_ALLOWED_USER_ID or str(user_id) != str(TELEGRAM_ALLOWED_USER_ID):
                        print(f"[Telegram Poller] Unauthorized user: {user_id}")
                        append_telegram_history({"user_id": user_id, "chat_id": chat_id, "command": text, "decision": "unauthorized"})
                        await send_telegram_message(chat_id, "Access denied: this Telegram user is not whitelisted for AJA.")
                        continue

                    reply = await execute_telegram_command(text, int(user_id), chat_id)
                    print(f"[Telegram Poller] Sending reply to {user_id}")
                    await send_telegram_message(chat_id, reply)
            else:
                error_msg = res.get("error", "")
                # If error is "Conflict", another bot instance might be running or a webhook is set
                if "409" in error_msg or "Conflict" in error_msg:
                    print(f"[Telegram Poller] Conflict (409): {error_msg}. Polling is blocked by a webhook or another instance.")
                    await asyncio.sleep(10)
                elif "401" in error_msg:
                    print(f"[Telegram Poller] Unauthorized (401): Your bot token is invalid.")
                    await asyncio.sleep(60)
                else:
                    # Don't spam the log for common timeouts
                    if "timeout" not in error_msg.lower():
                        print(f"[Telegram Poller] Fetch error: {error_msg}")
                
        except Exception as e:
            print(f"[Telegram Poller] Loop error: {e}")
            
        await asyncio.sleep(1)

@app.on_event("startup")
async def startup_event():
    if TELEGRAM_BOT_TOKEN:
        asyncio.create_task(telegram_polling_loop())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
