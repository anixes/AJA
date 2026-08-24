# Example 03: Natural Language Reminders

**Capability**: Chat-based one-shot reminders with natural time parsing
**Difficulty**: Beginner
**Prerequisites**: `aja serve` running (or `aja chat` in a session that persists until the reminder fires)

## Objective

Set reminders by typing plain English — no cron syntax required.

## Steps

```bash
aja chat
```

Then try these patterns:

```
remind me to call mom tomorrow at 3pm
remind me to check the oven in 25 minutes
remember to water the plants friday
/reminders
```

Snooze a fired reminder from chat:

```
snooze 10m
```

## Expected Output

AJA confirms each reminder. When the time arrives, the reminder message is delivered to the same platform/chat where it was created, and the job auto-deletes from the scheduler.

`/reminders` lists pending one-shot jobs.

## How It Works

The intent parser routes REMINDER utterances to the scheduler. The natural-time parser (`nl_time.py`, pure stdlib) converts phrases like "tomorrow at 3pm" or "in 2 hours" into timestamps; past times roll forward to the next occurrence. A one-shot CronScheduler job fires a bus event back to your ORIGIN chat, then removes itself.

## Troubleshooting

- **Reminder never fires**: the daemon must be running; reminders do not survive with AJA fully stopped.
- **Time parsed wrong**: be explicit ("3pm", "friday 9am"); ambiguous times default forward.
- **Delivered to wrong chat**: reminders deliver to the chat that created them — set it from the platform you use.
