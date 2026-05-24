# Phase 4: Messaging Layer

Phase 4 turns Assistant from a task tracker into a relationship manager. Assistant can draft, manage, approve, and track outbound communication without unsafe auto-send behavior.

## Goal

Assistant should help with communication while preserving operator control.

Examples:

- draft recruiter follow-up
- remind Rahul about project deadline
- send internship follow-up draft
- draft professional reply to recruiter
- check pending unanswered messages

## Storage

Messaging is stored in the existing LanceDB/Arrow secretary database:

```text
.agent/assistant_secretary.lancedb
```

Table:

```text
secretary_communications
```

## Core Communication Object

Each communication record supports:

- `message_id`
- `recipient`
- `channel`
- `subject`
- `draft_content`
- `tone_profile`
- `approval_required`
- `approval_status`
- `follow_up_required`
- `follow_up_due`
- `related_task_id`
- `communication_history`
- `delivery_status`
- `last_sent_at`
- `created_at`
- `updated_at`

## Workflow

```text
Draft -> Edit -> Approval -> Send -> Follow-up tracking
```

Rules:

- Assistant never auto-sends the first version.
- All outbound messages require approval.
- Sending is blocked unless `approval_status = approved`.
- Telegram is the only direct outbound adapter for now.
- Email and professional messages are drafts only until an adapter exists or the user sends them manually.
- Sent messages can create follow-up tasks when `follow_up_required` is true.

## Delivery Status

- `draft`
- `ready`
- `sent`
- `failed`
- `cancelled`

## Approval Status

- `pending`
- `approved`
- `rejected`
- `not_required`

Production policy uses `pending` by default for outbound communication.

## Interfaces

### CLI

```bash
python agent.py message draft "draft recruiter follow-up"
python agent.py message list
python agent.py message approve <message_id>
python agent.py message reject <message_id>
```

### FastAPI

- `GET /communications`
- `POST /communications`
- `GET /communications/{message_id}`
- `PATCH /communications/{message_id}`
- `POST /communications/{message_id}/edit`
- `POST /communications/{message_id}/approve`
- `POST /communications/{message_id}/reject`
- `POST /communications/{message_id}/send`
- `GET /communications/summary/mobile`

### Telegram

Assistant recognizes:

- `draft recruiter follow-up`
- `draft professional reply to recruiter`
- `send internship follow-up draft`
- `remind Rahul about project deadline`
- `approve message <message_id>`
- `reject message <message_id>`
- `edit message <message_id> <new text>`
- `send message <message_id>`
- `check pending unanswered messages`

`send message <message_id>` still requires prior approval. For email/professional drafts, Assistant keeps the message ready but does not pretend to send it through a missing adapter.

## Relationship Management

Recruiter and professional drafts use context-aware templates and default to a professional tone. Follow-up-required messages can create secretary tasks, so Assistant can track unanswered communication rather than merely drafting text.

