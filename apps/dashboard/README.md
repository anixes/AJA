# AgentX Core Dashboard for AJA

The dashboard is the Visual Command Center for AgentX Core. AJA uses it as a human-in-the-loop control surface for runtime state, approval requests, task telemetry, and security events exposed by `scripts/api_bridge.py`.

## Current Views

- **Approval Queue**: Shows the pending structured approval object and lets you approve or deny it directly from the dashboard
- **Task Board**: Streams baton task state from `temp_batons/`, including lifecycle stage, failures, recent worker output, and the latest baton history
- **Security Events**: Recent `ALLOW`, `ASK`, `DENY`, `APPROVED`, and `DENIED` runtime events
- **Territories**: Health cards for monitored project areas
- **Runtime Diff**: Current git diff snapshot from the bridge
- **Git History**: Recent commit history
- **Secretary Memory API**: SQLite-backed task memory is exposed through the bridge for future dashboard panels
- **Communication API**: Outbound drafts, approval state, delivery status, and follow-up tracking are exposed through the bridge
- **Scheduler API**: Morning, night, and weekly executive reviews are exposed for future dashboard review panels

## Data Contract

The dashboard now keeps a live connection open to:

- `GET /runtime/stream`

and still uses these action endpoints when the user clicks buttons:

- `POST /runtime/approve`
- `POST /runtime/deny`

Secretary memory endpoints are available for AJA task panels:

- `GET /memory/tasks`
- `POST /memory/tasks`
- `PATCH /memory/tasks/{task_id}`
- `POST /memory/tasks/{task_id}/complete`
- `POST /memory/tasks/{task_id}/archive`
- `GET /memory/review`
- `GET /memory/summary`

Communication endpoints are available for future relationship-management panels:

- `GET /communications`
- `POST /communications`
- `PATCH /communications/{message_id}`
- `POST /communications/{message_id}/edit`
- `POST /communications/{message_id}/approve`
- `POST /communications/{message_id}/reject`
- `POST /communications/{message_id}/send`
- `GET /communications/summary/mobile`

Scheduler endpoints are available for accountability panels:

- `GET /scheduler/config`
- `PATCH /scheduler/config`
- `GET /scheduler/review/{kind}`
- `POST /scheduler/review/{kind}/deliver`
- `POST /scheduler/run`
- `POST /scheduler/snooze/{task_id}`

The SSE stream emits a runtime snapshot containing:

- `status`
- `batons`
- `events`
- `diff`
- `history`

The bridge reads from the shared runtime state file at `.agentx/runtime-state.json`, which is written by the TypeScript runtime, and combines it with git/diff status into each stream payload.
Approve and deny actions are executed through `src/runtime_actions.ts` for CLI/runtime approvals. Telegram-originated approvals are executed by the FastAPI bridge so the dashboard and phone stay synchronized around the same `.agentx/runtime-state.json` object.

## Approval Object

Risky actions are shown as structured approval requests, not opaque command prompts. The dashboard expects the pending approval to include:

- `id`
- `commandPreview`
- `actionType`
- `humanReason`
- `riskLevel`
- `rollbackPath`
- `expiresAt`
- `requesterSource`
- `dryRunSummary`
- `reasons`

This is the same object AJA sends through Telegram for Phase 2 remote control.

## Runtime Notes

- The dashboard now uses **Server-Sent Events (SSE)** for live runtime updates.
- SSE snapshots are emitted from `scripts/api_bridge.py`.
- Pending approvals are single-item for now: one risky action can be awaiting review at a time.
- The bridge is state-driven: it reads `.agentx/runtime-state.json` rather than keeping its own in-memory approval queue.
- Approval decisions are written to `.agentx/approval-audit.jsonl` as append-only JSONL records.
- Secretary memory is stored in `.agentx/aja_secretary.sqlite3` and survives bridge restarts.
- Communication records are stored in the same SQLite database and require approval before delivery.
- Scheduler delivery events are stored in SQLite to avoid repeated review spam.

## Development

```bash
npm install
npm run dev
```

Build for production:

```bash
npm run build
```
