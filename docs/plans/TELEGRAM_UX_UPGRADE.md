# Telegram Interface Upgrade Plan — Hermes/OpenClaw-Grade

Research date: 2026-08-26 (overnight session)
Sources: hermes-agent.ai/integrations/telegram, docs.openclaw.ai/channels/telegram,
github.com/NousResearch/hermes-agent telegram.md, openclawhq.io

## What AJA has today vs. the reference agents

| Capability | Hermes | OpenClaw | AJA today |
|---|---|---|---|
| Bot DM + allowlist auth | ✅ numeric IDs / pairing codes | ✅ pairing default, dmPolicy | ✅ TELEGRAM_ALLOWED_USER_ID |
| Streaming replies (edit-in-place) | ✅ edit/draft transports | ✅ off/partial/block/progress modes | ❌ single message after completion |
| Tool-progress lines during turn | ✅ status bubbles edited in place | ✅ toolProgress preview lines | ❌ silence until done |
| Ack reaction while processing | — | ✅ ackReaction emoji | ❌ |
| Exec approvals w/ inline buttons | ✅ "reply yes to approve" | ✅ execApprovals + inline buttons, 30min TTL | ⚠️ mission-level only |
| Voice notes → STT → agent | ✅ faster-whisper/Groq/OpenAI | — | ❌ |
| MEDIA: file delivery (native attachments) | ✅ tag-based, huge ext list | ✅ mediaMaxMb 100 | ⚠️ truncation only |
| Command menu registration (setMyCommands) | ✅ auto, priority list, cap 60 | ✅ native + customCommands | ❌ |
| Group: mention-only, observe-without-reply | ✅ require_mention + observe mode | ✅ requireMention + historyLimit | ❌ |
| Forum topics = isolated sessions | ✅ DM topics + /topic multi-session | ✅ topic routing per agent | ❌ |
| Home channel for cron delivery | ✅ /sethome | — | ⚠️ scheduler delivers via adapters but no home-channel concept |
| Error policy control | — | ✅ always/once/silent | ❌ errors dump raw |
| Notifications volume control | ✅ important vs all | — | ❌ every message pings |
| Rich messages (tables etc.) | ✅ opt-in rich + MDv2 fallback | ✅ opt-in Bot API 10.2 | ❌ plain text |
| Webhook mode | ✅ for cloud auto-wake | ✅ durable ingress queue | ❌ polling only |

## Prioritized build plan

### Tier 1 — feel-alive basics (highest UX ROI, low risk)
1. **Ack reaction** 👀 on receipt → ✅ on reply (tg_client send_reaction; degrade silently if no permission). Cheapest "it's working" signal.
2. **Tool-progress status bubble**: one message sent at turn start ("🔧 Working…"), edited in place as tools run (reuse presenter/tool events), replaced by final answer. Mirrors Hermes `send_or_update_status` pattern.
3. **Command menu registration** (`setMyCommands`) at gateway startup from the REPL slash-command registry: /status /kanban /missions /models /help etc.
4. **Notification discipline**: `disable_notification=true` for progress edits; only final answers + approvals ring.

### Tier 2 — trust & control
5. **Per-command exec approvals**: pending-command store (TTL 5–30 min, one-shot), inline ✅/❌ buttons wired through tg_client._handle_callback; re-classify at execution time (byte-match TOCTOU guard); deny-list never overridable.
6. **MEDIA: file delivery** — parse `MEDIA:/path` tags in replies, ship as native Telegram documents (pdf/txt/md/csv/log/images...). Kills the 3500-char truncation problem properly.
7. **Error policy**: friendly error bubbles with retry hint instead of raw tracebacks; `errorPolicy: once` dedupe.

### Tier 3 — power features
8. **Voice notes → STT** (faster-whisper local first; AJA already has GPU) injected as text into the turn.
9. **Home channel** `/sethome` — cron/scheduler results delivered to designated chat (AJA's CronScheduler already publishes to bus → adapters; add a home-channel resolver).
10. **Group mention-mode + observe** — require @mention in groups, observe-but-don't-dispatch unmentioned messages into session context.
11. **Topic-per-session isolation** (Bot API 9.4 private chat topics) — parallel workspaces in one DM.

### Security invariants (all tiers)
- Owner allowlist enforced before any processing; reject "*" when exec features on.
- CommandGuard classify on every shell path; approval clears "ask" never "deny".
- Audit journal rows for EXEC_REQUESTED/APPROVED/COMPLETED(exit_code).
- Secrets redacted from all outbound text (redact_secrets already exists).

## Tonight's scope decision
Tier 1 items 1–4 are small, self-contained, and testable offline (mock updates through
gateway_runner.process_event). They slot AFTER briefing-offload in the queue if time
remains; otherwise they are the head of tomorrow's queue. Tier 2 item 5 (exec approvals)
is the next dedicated session's centerpiece together with wiring run_direct_loop into
/pc (agentic loop depth).
