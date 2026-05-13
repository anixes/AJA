# Phase 1 and 2: Assistant Remote Control and Approval Workflow

This document is the canonical reference for the Telegram control path and production approval workflow.

## Identity Split

- **Agent Core** is the engine: tools, runtime state, FastAPI bridge, dashboard, vault, safety gate, and swarm orchestration.
- **Assistant** is the assistant and operator: the personality layer that receives intent, explains consequences, and asks for approval when risk appears.

Agent Core powers Assistant.

## Phase 1: Telegram Remote Control

Goal: control the PC from a phone using Telegram.

Flow:

```text
Telegram Bot API -> FastAPI bridge -> Agent Core runtime -> safety gate -> execution layer
```

Implemented endpoints:

- `GET /telegram/status`: bridge configuration and pending count.
- `GET /telegram/history`: recent Telegram command history.
- **Local Long-Polling Loop**: Replaced brittle webhooks with a robust local polling loop in `bridge.py`. This ensures reliability behind NAT and avoids conflict with public-facing webhooks.

### Command Priority Logic
Telegram messages are processed with the following priority:
1. **Approval/Rejection**: Explicit confirmation of pending actions.
2. **Supported Shortcuts**: Hardcoded commands in `build_supported_command` (e.g., `check gpu`) execute immediately.
3. **Secretary Intent**: Natural language tasks, reminders, and drafts.
4. **General Chat**: fallback LLM interaction.

Required environment:

```bash
TELEGRAM_BOT_TOKEN=123456:bot-token
TELEGRAM_ALLOWED_USER_ID=123456789
TELEGRAM_WEBHOOK_SECRET=long-random-secret
```

Supported initial text commands:

- `status`
- `check gpu`
- `run training job`
- `git pull repo`
- `shutdown laptop tonight`
- `restart notebook process`

Security behavior:

- Only `TELEGRAM_ALLOWED_USER_ID` can issue commands.
- Text commands only.
- Unsupported commands are denied with an explanation.
- Incoming commands are appended to `.agent/telegram-history.jsonl`.
- Output is trimmed for mobile readability.

## Phase 2: Production Approval Workflow

Goal: every risky action must be understandable before approval.

Risky actions no longer use an opaque confirmation-token flow. Assistant now sends a structured approval request and waits for explicit operator approval.

Approval commands:

- Telegram: `approve <id>` or `reject <id>`
- Dashboard: Approve or Deny buttons
- CLI/runtime: `/approve` or `/deny`

## Approval Object

Every approval object includes:

- `id`: request ID
- `commandPreview`: exact command preview
- `actionType`: action category such as `git_update`, `scheduled_shutdown`, or `notebook_restart`
- `operatorReason`: readable reason for review
- `riskLevel`: `low`, `medium`, or `high`
- `rollbackPath`: safe rollback or recovery path when known
- `expiresAt`: expiration timestamp
- `requesterSource`: `CLI`, `dashboard`, `Telegram`, or `swarm`
- `dryRunSummary`: expected effect before execution
- `reasons`: detailed safety reasons

Example:

```json
{
  "id": "approval-1777482768-2487",
  "commandPreview": "git pull --ff-only",
  "actionType": "git_update",
  "operatorReason": "Updates the repository working tree.",
  "riskLevel": "medium",
  "rollbackPath": "Use git reflog to find the previous HEAD, then reset only after reviewing local changes.",
  "requesterSource": "Telegram",
  "dryRunSummary": "Would fetch and fast-forward the current repository only if Git can do so without a merge commit."
}
```

## Dashboard Sync

Telegram-originated approvals are written to `.agent/runtime-state.json` as `pendingApproval`, so the dashboard sees the same object the phone receives.

Dashboard decisions on Telegram-originated approvals execute through the FastAPI bridge and notify the Telegram chat. CLI/runtime approvals still execute through `src/runtime_actions.ts`.

## Audit and Persistence

- `.agent/runtime-state.json`: current shared runtime state and pending approval.
- `.agent/telegram-history.jsonl`: Telegram command history.
- `.agent/telegram-pending.json`: compatibility store for Telegram pending IDs.
- `.agent/approval-audit.jsonl`: append-only approval lifecycle log.

## Execution Rules

- No hidden execution for risky commands.
- Default behavior for risky action is ASK.
- Denied commands explain why.
- Approval tokens expire.
- Approved commands are revalidated before execution.
- Rollback guidance is included whenever Assistant can provide one safely.

## Current Limits

- Pending approvals are single-item in `.agent/runtime-state.json`.
- Telegram supports text commands only.
- Shell analysis is heuristic and uses `CommandStripper`, not a full shell AST parser yet.
