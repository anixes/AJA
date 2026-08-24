# Example 06: Scheduled Website Monitoring to Telegram

**Capability**: Cron-scheduled research missions with report delivery
**Difficulty**: Intermediate
**Prerequisites**: Telegram bot token + your user ID in the allowlist (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS`); `aja serve` running continuously

## Objective

Have AJA check python.org every Monday at 9:00 for new releases and push a summary to your Telegram.

## Steps

1. Verify gateway auth is configured (fail-closed otherwise):

```bash
aja doctor
```
Confirm the Gateway Auth Posture shows telegram SECURE.

2. Register the weekly job via chat or CLI:

```bash
aja chat
```
```
every monday at 9am, check python.org/downloads/ for any new Python release and summarize changes since last week. Send me the report.
```

Or add it programmatically with a standard 5-field cron string through the scheduler:

```
schedule research job: cron "0 9 * * 1" — fetch python.org downloads page and report new stable releases
```

3. Keep AJA running:

```bash
aja serve
```

## Expected Output

Every Monday 09:00 you receive a Telegram message summarizing current Python versions and anything new. Repeated weeks show "no changes" when nothing was released.

## How It Works

CronScheduler parses the 5-field cron expression and spawns a mission on schedule (jobs run under `AJA_JOB_TIMEOUT_S`, default 600s). The worker uses `fetch_url` on python.org; results are captured into the job's `last_report` and published as `MISSION_COMPLETED` on the bus, which telemetry tails deliver to your chat automatically.

## Troubleshooting

- **No message**: confirm `aja serve` was alive at fire time; missed ticks do not replay.
- **Auth denied in Telegram**: your user ID must be in `TELEGRAM_ALLOWED_USER_IDS`.
- **Job times out on slow networks**: raise `AJA_JOB_TIMEOUT_S` above the default 600.
- **Duplicate jobs**: list scheduled jobs first before re-adding the same cron entry.
