"""
Extracted from api/bridge.py — Telegram gateway helpers and communication formatting.

Pure functions and async service utilities; no FastAPI imports. Memory access via
get_aja_memory() or injected memory provider.
"""

import asyncio
from datetime import datetime, timedelta
import json
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional
import urllib.parse
import urllib.request

from aja.api.services.priority_engine import run_priority_engine
from aja.memory.secretary import (
    format_communication_for_mobile,
    format_tasks_for_mobile,
    get_aja_memory,
    parse_communication_intent,
    parse_task_intent,
)
from aja.utils.redact import redact_secrets

logger = logging.getLogger(__name__)


def compact_text(value: str, limit: int = 1800) -> str:
    text = (value or "").strip()
    if not text:
        return "(no output)"
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if len(text) <= limit:
        return text
    return text[: limit - 80].rstrip() + "\n\n... output trimmed for Telegram."


def _escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    escaped = text
    for char in special_chars:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


async def send_telegram_message(
    chat_id: int | str,
    text: str,
    parse_mode: str = "MarkdownV2",
    bot_token: Optional[str] = None,
):
    """Robust message delivery to Telegram with auto-escaping for MarkdownV2."""
    token = bot_token or os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN is not configured."}

    formatted_text = text
    if parse_mode == "MarkdownV2":
        if "\\" not in text or not any(c in text for c in "_*[]()"):
            formatted_text = _escape_mdv2(text)

    def _send():
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": str(chat_id),
            "text": compact_text(formatted_text, 3900),
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return await asyncio.to_thread(_send)
    except Exception as exc:
        return {"ok": False, "description": redact_secrets(str(exc))}


def get_telegram_message(update: dict) -> dict:
    return update.get("message") or update.get("edited_message") or {}


def format_status_for_mobile(payload: dict) -> str:
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


def build_aja_help() -> str:
    return "\n".join(
        [
            "AJA memory",
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


def generate_definition_of_done(objective: str) -> List[str]:
    """
    Auto-generate a Definition of Done from an objective string.
    AJA refuses to delegate without clear success criteria.
    """
    o = objective.lower()
    items: List[str] = [
        "Goal achieved as described in the brief",
        "Handoff summary or output note provided",
    ]

    if any(
        k in o
        for k in (
            "code",
            "build",
            "implement",
            "create",
            "write",
            "develop",
            "scaffold",
            "generate",
        )
    ):
        items += [
            "Code reviewed and clean",
            "Unit tests pass",
            "No secret or key leakage",
        ]

    if any(
        k in o
        for k in (
            "auth",
            "login",
            "token",
            "session",
            "security",
            "password",
            "credential",
        )
    ):
        items += [
            "Authentication works end-to-end",
            "Rollback path documented",
            "No credentials hardcoded",
        ]

    if any(k in o for k in ("fix", "debug", "resolve", "patch", "repair", "bug")):
        items += [
            "Root cause identified and documented",
            "Fix verified with test",
            "No regressions introduced",
        ]

    if any(k in o for k in ("refactor", "clean", "restructure", "reorganize")):
        items += [
            "Behaviour unchanged (no regressions)",
            "Readability improved",
            "PR summary generated",
        ]

    if any(k in o for k in ("test", "verify", "validate", "check", "qa")):
        items += ["All cases pass (happy path + edge cases)", "Results documented"]

    if any(k in o for k in ("deploy", "release", "publish", "ship", "launch")):
        items += [
            "Deployment verified in target environment",
            "Health checks pass",
            "Rollback plan documented",
        ]

    if any(k in o for k in ("email", "message", "reply", "send", "draft", "notify")):
        items += [
            "Message content reviewed and approved",
            "Recipient confirmed",
            "Tone appropriate for context",
        ]

    if any(
        k in o
        for k in ("research", "find", "analyze", "analyse", "report", "investigate")
    ):
        items += [
            "Findings documented with sources",
            "Conclusions are actionable",
            "Gaps / unknowns flagged",
        ]

    if any(
        k in o
        for k in (
            "apply",
            "application",
            "resume",
            "cv",
            "recruiter",
            "interview",
            "job",
        )
    ):
        items += [
            "Application submitted and confirmation received",
            "Follow-up reminder set",
            "Status logged in AJA memory",
        ]

    if any(k in o for k in ("payment", "pay", "bill", "invoice", "transfer")):
        items += ["Transaction confirmed", "Receipt logged", "Amount verified"]

    if any(k in o for k in ("pr", "pull request", "merge")):
        items += [
            "PR description complete",
            "Review comments addressed",
            "Merge approved by owner",
        ]

    # Always append PR summary for engineering tasks
    if any(
        k in o
        for k in ("code", "build", "implement", "fix", "debug", "deploy", "refactor")
    ):
        items.append("PR summary or handoff note generated")

    # Deduplicate preserving order
    seen: set[str] = set()
    result: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


async def send_communication_if_supported(
    message: dict,
    memory_provider: Optional[Callable[[], Any]] = None,
    bot_token: Optional[str] = None,
):
    if message["approval_status"] != "approved":
        return {"ok": False, "message": "Message is not approved yet."}
    if message["channel"] != "telegram":
        return {
            "ok": False,
            "message": "No direct send adapter is configured for this channel. The approved draft remains ready for manual sending.",
        }

    result = await send_telegram_message(
        message["recipient"], message["draft_content"], bot_token=bot_token
    )
    if not result.get("ok"):
        return {
            "ok": False,
            "message": f"Telegram send failed: {result.get('description', 'unknown error')}",
        }
    mem = memory_provider() if memory_provider else get_aja_memory()
    sent = mem.mark_communication_sent(
        message["message_id"], "Sent through Telegram Bot API."
    )
    return {
        "ok": True,
        "message": f"Sent Telegram message {sent['message_id']} to {sent['recipient']}.",
    }


async def deliver_executive_review(
    kind: str,
    chat_id: int | str | None = None,
    force: bool = False,
    memory_provider: Optional[Callable[[], Any]] = None,
    bot_token: Optional[str] = None,
    allowed_user_id: Optional[str] = None,
):
    memory = memory_provider() if memory_provider else get_aja_memory()
    if not force and kind not in memory.due_review_kinds():
        return {"ok": False, "message": f"{kind} review is not due."}
    review = await asyncio.to_thread(memory.generate_executive_review, kind, True)
    target_chat = (
        chat_id
        or os.getenv("TELEGRAM_REVIEW_CHAT_ID")
        or allowed_user_id
        or os.getenv("TELEGRAM_ALLOWED_USER_ID")
    )
    if not target_chat:
        return {"ok": False, "message": "No Telegram review chat is configured."}
    result = await send_telegram_message(target_chat, review["summary"], bot_token=bot_token)
    if not result.get("ok"):
        return {
            "ok": False,
            "message": f"Telegram delivery failed: {result.get('description', 'unknown error')}",
        }
    event = await asyncio.to_thread(
        memory.record_scheduler_event,
        f"{kind}_review",
        str(target_chat),
        {"summary": review["summary"]},
        True,
    )
    return {"ok": True, "review": review, "event": event}


def execute_aja_command_sync(
    text: str,
    source: str,
    owner: str = "AJA",
    memory_provider: Optional[Callable[[], Any]] = None,
):
    normalized = " ".join((text or "").strip().split())
    lowered = normalized.lower()
    memory = memory_provider() if memory_provider else get_aja_memory()

    if lowered in {"tasks", "task summary", "aja summary", "memory summary"}:
        return memory.summary()

    if lowered in {"task help", "aja help", "memory help"}:
        return build_aja_help()

    if lowered in {"task review", "review tasks", "aja review"}:
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
        focus = [
            t for t in result["top3"] if t.get("urgency_tier") in ("critical", "high")
        ]
        ignore = result.get("ignore_candidates", [])
        lines = []
        if focus:
            lines.append("What actually matters today:\n")
            for item in focus:
                lines.append(f"• {item['title']}")
                lines.append(f"  → {item.get('decision_recommendation', '')}")
                if item.get("urgency_challenge"):
                    lines.append(f"  Note: {item['urgency_challenge']}")
                lines.append("")
        else:
            lines.append(
                "Nothing truly critical today. Consider working on medium-priority items."
            )
        if ignore:
            lines.append(
                f"\nSafe to defer: {', '.join(t['title'] for t in ignore[:3])}"
            )
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
            reason = item.get(
                "ignore_reason", "Low urgency and low consequence of delay."
            )
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
            return f"No AJA task found for {task_id}."

    for prefix, action in (
        ("complete ", "complete"),
        ("done ", "complete"),
        ("archive ", "archive"),
    ):
        if lowered.startswith(prefix):
            task_id = normalized.split(maxsplit=1)[1].strip()
            try:
                task = (
                    memory.complete_task(task_id)
                    if action == "complete"
                    else memory.archive_task(task_id)
                )
                return f"{action.title()}d: {task['title']}\nid: {task['task_id']}\nstatus: {task['status']}"
            except KeyError:
                return f"No AJA task found for {task_id}."

    if lowered in {
        "communications",
        "communication summary",
        "drafts",
        "message drafts",
        "check pending unanswered messages",
    }:
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
            message = memory.edit_communication(
                parts[2], parts[3], "Edited from command."
            )
            return format_communication_for_mobile(message)
        except KeyError:
            return f"No message found for {parts[2]}."

    task_data = parse_task_intent(normalized, source=source, owner=owner)
    if task_data:
        task = memory.create_task(task_data)
        due = task.get("due_date") or "no due date"
        return "\n".join(
            [
                "Saved AJA task",
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


def seconds_until_tonight(hour: int = 23, minute: int = 30) -> int:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60, int((target - now).total_seconds()))


def build_supported_command(text: str) -> dict:
    normalized = " ".join((text or "").strip().lower().split())
    if normalized in {"/start", "help", "/help"}:
        return {
            "kind": "help",
            "message": "\n".join(
                [
                    "AJA Telegram Gateway active.",
                    "Mission status: Operational.",
                    "Available commands:",
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
        return {
            "kind": "execute",
            "command": "nvidia-smi",
            "requires_confirmation": False,
            "action_type": "gpu_check",
            "risk_level": "low",
        }
    if normalized == "run training job":
        return {
            "kind": "execute",
            "command": f'"{sys.executable}" -m aja run --bg "run training job"',
            "requires_confirmation": True,
            "reason": "Starts a background AJA mission powered by AJA Core.",
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


def build_aja_chat_context(limit: int = 5, memory_provider: Optional[Callable[[], Any]] = None) -> str:
    memory = memory_provider() if memory_provider else get_aja_memory()
    context: List[str] = []
    try:
        # 1. Active Tasks
        tasks = memory.list_tasks(
            statuses=["pending", "active", "blocked"], limit=limit
        )
        if tasks:
            context.append("Current tasks:")
            for task in tasks:
                due = task.get("due_at") or task.get("due_date") or "no due date"
                context.append(
                    f"- {task.get('title')} [{task.get('priority')}, due {due}]"
                )

        # 2. Pending Approvals
        approvals = memory.list_approvals(statuses=["pending"], limit=limit)
        if approvals:
            context.append("\nPending Approvals:")
            for app in approvals:
                context.append(
                    f"- [{app.get('id')}] {app.get('action_type')}: {app.get('command')[:50]}..."
                )

    except Exception as e:
        logger.error(f"Error building chat context: {e}")

    return "\n".join(context) or "No current task context is available."


def generate_aja_chat_reply(
    text: str,
    user_id: int,
    chat_id: int | str,
    memory_provider: Optional[Callable[[], Any]] = None,
) -> str:
    """
    Generate a natural language reply using the AJA Executive Brain (LLM).
    Context includes recent tasks, pending approvals, and chat history.
    """
    mem = memory_provider() if memory_provider else get_aja_memory()
    # 1. Fetch Context
    context_data = build_aja_chat_context(limit=5, memory_provider=memory_provider)

    # 2. Fetch Recent History from LanceDB
    history_rows = mem.get_communication_history(f"telegram:{user_id}", limit=3)
    history_context = ""
    if history_rows:
        history_context = "Recent history:\n" + "\n".join(
            [
                f"User: {h.get('content')}\nAJA: {h.get('draft_content')}"
                for h in reversed(history_rows)
                if h.get("content")
            ]
        )

    system_prompt = (
        "You are AJA (Assistant of Joint Agents), a highly capable AI assistant and personal secretary "
        "designed to manage your operator's AJA swarm, obligations, and system tasks. "
        "Your tone is polite, refined, deeply loyal, and helpful (e.g., using terms like 'Sir', 'My friend', 'Operator', or 'Indeed'), "
        "yet you remain fully developer-fluent, casual, and possess a sharp conversational intelligence—witty, concise, and brilliant. "
        "Always prioritize organizing tasks, scheduling meetings, and delivering structured, clean briefings when providing system updates. "
        "Use the provided context to answer specifically. If a task is blocked or an approval is pending, mention it politely. "
        "Never hallucinate system state. Keep replies natural, helpful, and under 1000 characters for mobile readability."
    )

    full_prompt = (
        f"CONTEXT:\n{context_data}\n\n"
        f"{history_context}\n\n"
        f"USER MESSAGE: {text}\n\n"
        "REPLY:"
    )

    try:
        from aja.llm import completion

        reply = completion(full_prompt, system_prompt=system_prompt)

        if not reply or len(reply.strip()) < 2:
            return "I'm here, but the LLM provider returned an empty response. Check your API keys."

        # Record this communication in LanceDB for future context
        mem.create_communication(
            {
                "recipient": f"telegram:{user_id}",
                "content": text,
                "draft_content": reply,
                "channel": "telegram",
                "approval_status": "approved",
            }
        )

        return compact_text(reply, 1800)
    except Exception as e:
        logger.error(f"[Chat] Generation failed: {e}")
        return f"I encountered an error while thinking: {str(e)[:100]}..."
