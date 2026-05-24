# Phase 3: Structured Secretary Memory

Phase 3 gives Assistant persistent executive-assistant memory. This is a hybrid memory system combining a **structured LanceDB/Arrow task system** for obligations with a **high-performance Semantic RAG (Vector)** layer for project-wide knowledge retrieval.

## Goal

Assistant should remember what needs to be done, when it matters, who is involved, whether approval is required, and whether a commitment is going stale.

Examples:

- remind me if I skip gym
- follow up with recruiter next Tuesday
- internship application status check
- bill payment reminder
- project deadline accountability

## Storage

LanceDB/Arrow database:

```text
.agent/assistant_secretary.lancedb
```

Runtime files are ignored by git:

- `.agent/assistant_secretary.lancedb`
- `.agent/assistant_secretary.lancedb-*`

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

## Territory Knowledge (Semantic RAG)

The `territory_knowledge` table enables the agent to search project files semantically. It uses real embeddings to understand context beyond simple keyword matching.

### Schema:
- `id`: Unique chunk identifier.
- `path`: Relative path to the source file.
- `content`: Raw text content of the chunk.
- `metadata_json`: Rich metadata (line numbers, chunk index, extension).
- `updated_at`: Last index timestamp.
- `vector`: **384-dimension float array** (optimized for `all-MiniLM-L6-v2`).

### Capabilities:
- **Territory Scanning**: Automated project-wide indexing of supported files (.py, .ts, .md, etc.).
- **Semantic Search**: Retrieval of relevant context based on vector similarity rather than exact strings.
- **Idempotent Updates**: Automatic cleanup of old chunks before re-scanning paths.

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

When a recurring task is completed, Assistant schedules the next occurrence instead of losing the responsibility.

## Review and Escalation

The scheduled review path detects:

- overdue tasks
- tasks due soon
- stale tasks
- blocked tasks

Stale tasks can increment `escalation_level`, giving Assistant a way to notice ignored commitments and become firmer in later summaries.

## Interfaces

### CLI

```bash
python agent.py memory add "follow up with recruiter next Tuesday"
python agent.py memory list
python agent.py memory review
python agent.py memory complete <task_id>
python agent.py memory archive <task_id>
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

Assistant recognizes secretary commands from Telegram:

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

- Memory is structured and semantic, not guessed from chat logs.
- Assistant tracks obligations and project-wide context.
- LanceDB/Arrow is the authoritative source of truth.
- Vector memory is utilized for "Territory Awareness" (RAG).
- Secretary behavior and context-retrieval are core Phase 3 features.

