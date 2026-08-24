# AJA Examples

Copy-pasteable walkthroughs, each completable in under 5 minutes.

| # | Example | Capability | Difficulty |
|---|---------|-----------|------------|
| 01 | [Web Research](01-web-research.md) | search_web + fetch_url with cited summary | Beginner |
| 02 | [Daily Briefing](02-daily-briefing.md) | Morning digest: tasks + reminders + calendar | Beginner |
| 03 | [Natural Reminders](03-natural-reminders.md) | Chat-based one-shot reminders | Beginner |
| 04 | [Code Analysis](04-code-analysis.md) | Repo test-coverage audit via shell tools | Intermediate |
| 05 | [Dual-Model Split](05-dual-model-split.md) | Cloud planner + local GPU worker | Intermediate |
| 06 | [Scheduled Monitoring](06-scheduled-monitoring.md) | Weekly cron research mission → Telegram | Intermediate |
| 07 | [Browser Automation](07-browser-automation.md) | Playwright navigate/extract/wait patterns | Advanced |
| 08 | [Fleet Multi-Host](08-fleet-multi-host.md) | Baton transfer between hosts | Advanced |

## Quick Start

```bash
aja doctor          # verify installation
aja run "hello" --dry-run   # simulate a mission
aja serve           # start the daemon (needed for examples 02, 03, 06)
```

See `docs/operator/` for deployment guides (FLEET.md, CALENDAR.md, VPS.md).
