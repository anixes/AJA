# Phase 3: Structured Secretary Memory

Phase 3 gives AJA persistent executive-assistant memory. This is not chat-history recall and it is not a vector database. It is a structured LanceDB/Arrow task system for obligations, follow-ups, recurring responsibilities, and accountability commitments.

## Goal

AJA should remember what needs to be done, when it matters, who is involved, whether approval is required, and whether a commitment is going stale.

Examples:

- remind me if I skip gym
- follow up with recruiter next Tuesday
- internship application status check
- bill payment reminder
- project deadline accountability

## Storage

LanceDB/Arrow database:

```text
.agentx/aja_secretary.lancedb
```

Runtime files are ignored by git:

- `.agentx/aja_secretary.lancedb`
- `.agentx/aja_secretary.lancedb-*`

## Core Task Object

The `secretary_tasks` table stores:

- `task_id`
- `title`
- `context`
- `owner`
- `due_date`
- `recurrence`
- `priority`
- `status`
- `follow_up_state`
- `reminder_state`
- `escalation_level`
- `approval_required`
- `approval_status`
- `related_people`
- `communication_history`
- `source`
- `last_reviewed_at`
- `created_at`
- `updated_at`

Structured JSON fields are stored as JSON text in LanceDB/Arrow where appropriate.

## Status Values

- `pending`
- `active`
- `blocked`
- `completed`
- `archived`

## Priority Values

- `low`
- `medium`
- `high`
- `urgent`

Priority sorting is built into task listing: urgent and high-priority tasks appear first, then earlier due dates.

## Recurrence

Supported recurrence frequencies:

- `daily`
- `weekly`
- `monthly`
- `yearly`

When a recurring task is completed, AJA schedules the next occurrence instead of losing the responsibility.

## Review and Escalation

The scheduled review path detects:

- overdue tasks
- tasks due soon
- stale tasks
- blocked tasks

Stale tasks can increment `escalation_level`, giving AJA a way to notice ignored commitments and become firmer in later summaries.

## Interfaces

### CLI

```bash
python agentx.py memory add "follow up with recruiter next Tuesday"
python agentx.py memory list
python agentx.py memory review
python agentx.py memory complete <task_id>
python agentx.py memory archive <task_id>
```

### FastAPI

- `GET /memory/tasks`
- `POST /memory/tasks`
- `GET /memory/tasks/{task_id}`
- `PATCH /memory/tasks/{task_id}`
- `POST /memory/tasks/{task_id}/complete`
- `POST /memory/tasks/{task_id}/archive`
- `GET /memory/review`
- `GET /memory/summary`

These endpoints require the same bearer token as the other protected bridge actions.

### Telegram

AJA recognizes secretary commands from Telegram:

- `tasks`
- `task review`
- `complete <task_id>`
- `archive <task_id>`
- `add task <title> due <date> priority <low|medium|high|urgent>`

Natural task-like messages are also accepted, for example:

- `remind me if I skip gym every day`
- `follow up with recruiter next Tuesday`
- `bill payment reminder due tomorrow priority high`

Telegram summaries are compact and mobile-readable.

## Design Rules

- Memory is structured, not guessed from chat logs.
- AJA tracks obligations, not just conversations.
- LanceDB/Arrow is the source of truth for Phase 3.
- Vector memory is intentionally deferred.
- Secretary behavior is more important than chatbot recall.

