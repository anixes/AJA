# Phase 5: Scheduler and Daily Executive Review

Phase 5 makes AJA proactive. Instead of waiting for the user to ask what is unfinished, AJA can generate concise executive reviews, detect avoidance patterns, escalate ignored commitments, and deliver high-signal summaries through Telegram.

## Goal

AJA actively manages:

- unfinished tasks
- missed deadlines
- urgent follow-ups
- important pending communication
- recurring obligations
- personal accountability commitments

## Review Types

### Morning Review

Includes:

- unfinished tasks
- missed deadlines
- urgent follow-ups
- important communication pending
- top 3 priorities today

### Night Review

Includes:

- completed tasks
- missed commitments
- ignored reminders
- unfinished carry-forward actions
- tomorrow's critical focus

### Weekly Review

Includes:

- slipped commitments
- stale or avoided work
- blocked tasks
- communication follow-ups
- next-week priorities

## Scheduler Storage

The scheduler uses the existing LanceDB/Arrow secretary database:

```text
.agentx/aja_secretary.lancedb
```

Tables:

- `scheduler_settings`
- `scheduler_events`

`scheduler_events` prevents spam by recording delivered reviews. Morning/night reviews are delivered at most once per day. Weekly reviews are delivered at most once per calendar week.

## Scheduler Settings

Configurable settings include:

- `enabled`
- `morning_review_window`
- `night_review_window`
- `weekly_review_window`
- `weekly_review_weekday`
- `due_soon_hours`
- `stale_after_days`
- `max_daily_reminders`
- `telegram_delivery_enabled`
- `accountability_escalation_threshold`

## Accountability Behavior

AJA scores urgency using:

- priority
- due date proximity
- overdue state
- blocked state
- escalation level
- pending approval state

Repeatedly ignored tasks increment `escalation_level`. Delayed communication follow-ups can also escalate related tasks.

Example accountability tone:

```text
You said this mattered. Either do it today or remove it honestly.
```

## Snooze

Tasks can be snoozed without deleting the obligation. Snooze metadata is stored in `reminder_state`.

Telegram/CLI/API examples:

```text
snooze <task_id> tomorrow
```

```bash
POST /scheduler/snooze/{task_id}
```

## Interfaces

### CLI

```bash
python agentx.py review morning
python agentx.py review night
python agentx.py review weekly
```

### FastAPI

- `GET /scheduler/config`
- `PATCH /scheduler/config`
- `GET /scheduler/review/{kind}`
- `POST /scheduler/review/{kind}/deliver`
- `POST /scheduler/run`
- `POST /scheduler/snooze/{task_id}`

### Telegram

AJA recognizes:

- `morning review`
- `night review`
- `weekly review`
- `what am I avoiding today`
- `what slipped this week`
- `why is recruiter follow-up still pending`
- `snooze <task_id> tomorrow`

Telegram delivery is required for scheduled reviews. Use `TELEGRAM_REVIEW_CHAT_ID` to override the default review chat; otherwise AJA falls back to `TELEGRAM_ALLOWED_USER_ID`.

## No-Spam Rules

- Reviews are concise.
- Review delivery is windowed.
- Delivered review events are recorded.
- A due review is not delivered repeatedly in the same period.
- AJA escalates repeated avoidance instead of sending noisy repeated pings.

