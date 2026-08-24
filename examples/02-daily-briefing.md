# Example 02: Morning Daily Briefing

**Capability**: Composed daily briefing — tasks, reminders, calendar, research digests
**Difficulty**: Beginner
**Prerequisites**: `aja serve` (or any long-running process) so cron can fire; optional Google Calendar via `aja calendar connect`

## Objective

Get one structured morning message with overdue tasks, today's schedule, pending reminders, and priority focus — delivered to your chat platform.

## Steps

1. Enable the briefing at 7:00 AM daily:

```bash
aja briefing enable --at "0 7 * * *"
```

2. (Optional) Connect your calendar so briefings include events:

```bash
aja calendar connect
```

3. Start the daemon:

```bash
aja serve
```

4. Give AJA something to report on:

```bash
aja chat
```
```
add task: review pull request #42
remind me to submit expenses at 5pm
```

## Expected Output

At 07:00 the next morning you receive a structured markdown message in your connected chat (Telegram/Discord/Slack) with sections: Overdue, Today (tasks + calendar), Reminders, Priority Focus, and Overnight Research.

## How It Works

The briefing composer queries task storage, CronScheduler reminders, and the bi-temporal calendar graph (`events_between`), then publishes the composed message on the event bus; telemetry tails deliver it to your chat.

## Troubleshooting

- **No briefing arrived**: confirm `aja serve` was running at the scheduled time.
- **Re-running enable duplicates?** No — registration is idempotent.
- **Calendar section empty**: run `aja calendar connect` or check token expiry.
