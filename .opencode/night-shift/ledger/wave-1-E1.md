# Night-Shift Wave 1 — E1 Execution Report

Executor: E1 · Date: 2026-08-25 · Repo: `D:\AgenticAI\Project1(no-name)` (py -3.12)

Peer briefs consumed: `.opencode/night-shift/briefs/wave-1/T1.md` (primary), `A1.md`, `T4.md`.

## Files touched (exclusive claim honored)

| File | Fixes |
|---|---|
| `libs/aja-core/aja/gateway/tg_client.py` | T1#1, A1#4 |
| `libs/aja-core/aja/gateway/adapters/discord_adapter.py` | T1#1, A1#4 |
| `libs/aja-core/aja/gateway/adapters/slack_adapter.py` | T1#2, T1#4 |
| `libs/aja-core/aja/gateway/gateway_runner.py` | A1#3 |
| `libs/aja-core/aja/gateway/persistence.py` | T1#5 |
| `libs/aja-core/aja/persistence/tracker.py` | T1#1 (source-side schema unification) |
| `libs/aja-core/aja/gateway/orchestrator.py` | T1#3 (mission-intent block only, L597+; telemetry-tail call sites were already off-loop at L449/L487/L713 — no change needed there) |

## Fixes applied

### 1. T1#1 CRITICAL — NULL-status rows crashed both telemetry pollers & were permanently dropped
- **tg_client.py `_poll_lancedb_events`** + **discord_adapter.py `_poll_lancedb_events`**:
  - `status = (ev.get("status") or "success").upper()` (`.get(k, default)` does not fire for explicit NULL Arrow cells).
  - Per-event processing wrapped in its own `try/except` (`CancelledError` re-raised) so one bad row no longer aborts the rest of the batch.
  - `seen_event_ids.add(eid)` moved AFTER successful `telemetry_queue.put(payload)` — a crashing row now retries next tick instead of being silently dropped forever. Bounded-size trim retained right after the add.
- **persistence/tracker.py**: `log_event()` rows unified onto the shared `RUNTIME_EVENTS_SCHEMA` shape (`kind`/`target`/`status`/`command`/`metadata_json`/`timestamp`) so tracker writers no longer produce NULL status cells at the source. `get_events_by_task_id()` adapted to the new column names (`kind`, `timestamp`; no external consumers of the old keys exist). `print` → module `logger`.

### 2. T1#2 HIGH — SlackAdapter missing tail contract → MISSION intent AttributeError
- Added `_tail_tasks` / `_chat_queues` state plus `start_tail(chat_id)`, `tail_events(chat_id)`, `stop_tails()` matching the Telegram/Discord adapter interface (tracked task lifecycle, bounded per-channel queue, forwards `[STATUS] message` via `send_notification`). `stop()` now calls `stop_tails()`. MISSION intent on Slack no longer crashes and the confirmation reply sends.

### 3. T1#3 HIGH — `create_mission()` returning None → TypeError
- orchestrator mission block: `mission["mission_id"]` indexing now guarded by `if not mission or not mission.get("mission_id"):` → logs error (secret-redacted goal) and sets a user-visible "couldn't register the mission… try again" reply. `update_mission` / swarm confirmation skipped for the failure path.

### 4. T1#4 MEDIUM — Slack NULL-text coerced into phantom vision prompt
- slack_adapter envelope builder: `text_content = event.get("text") or ""`; text-less events (attachment-only / file_share subtypes) dropped at the adapter boundary with a debug log — mirrors the tg/discord no-text-no-media rule. The orchestrator vision-default coercion is thereby never fed an empty-text Slack event (kept inside my file claim; no orchestrator L452 change required).

### 5. T1#5 MEDIUM — `GatewayState.get_session` non-dict JSON wedged chats
- Post-load shape validation: parsed payload must be a dict, else fresh default `{"history": [], "metadata": {}}`. Valid dicts get `setdefault("history"/"metadata")` so callers can rely on the full default shape.

### 6. A1#3 HIGH — non-atomic `telegram_adapter` swap in GatewayRunner
- `process_event` body serialized behind a single `asyncio.Lock` held across the swap + `handle_gateway_event` await. Concurrent pollers can no longer clobber each other's adapter (cross-platform reply routing / data-leak class bug). Documented trade-off in code comment: gateway event handling is now serialized across platforms; the cleaner fix (explicit responder param through `handle_gateway_event`) requires an orchestrator signature change owned by another executor — flagged as deferred below.

### 7. A1#4 HIGH — sync LanceDB session IO on the loop in telemetry tails
- tg_client + discord_adapter `tail_events`: `get_session` / `update_session` wrapped in `await asyncio.to_thread(...)`.

## Test results

New suite `tests/python/unit/test_nightshift_wave1_e1.py` — **15 passed**:
- NULL-status row sanitized (telegram+discord), retried-after-failed-enqueue (telegram+discord) — proves seen-mark ordering.
- Tail session IO off-loop assertion (telegram+discord).
- Slack tail-contract presence, tail forwarding end-to-end, NULL-text drop guard.
- Orchestrator: create_mission→None failure reply (no update_mission, no crash), success path regression, falsy-dict `{}` result also guarded.
- GatewayRunner: concurrent cross-adapter events each observe their own proxy installed; swap restored after.
- GatewayState.get_session: null/string/list/garbage JSON → clean default shape; valid dict preserved with defaults filled.

Mandated verification command:

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave1_e1.py tests/python/unit -k "telegram or discord or slack or gateway or telemetry or session" -q --timeout=300 -p no:cacheprovider
→ 91 passed, 638 deselected (23s)
```

All 7 changed files pass `py_compile`. No dependency changes; logger-only output; minimal diffs.

## Deferred / notes for council

1. **A1#3 root fix**: lock-based serialization trades concurrency for correctness. The architectural fix — pass the adapter explicitly into `handle_gateway_event(event, responder=...)` — needs a signature change across orchestrator call sites (outside my claim scope). Recommend a follow-up wave owns that refactor and then removes `_event_lock`.
2. **Slack telemetry producers**: SlackAdapter now honors the full tail *contract*, but nothing enqueues into its chat queues yet (Discord has the same gap — its `_subscribe_bus_events` is defined but never called anywhere). If Slack tails should carry live bus telemetry/approvals, a producer-wiring task is needed for both adapters.
3. **Slack socket connect task (A1 F6)**: fire-and-forget `create_task(self._socket_client.connect())` still unreferenced — not in my assigned FIXES list, left untouched.
4. **Orchestrator latent items from T1 brief** (F6 `"AJA Warning" in content` with None content, F7 STATUS sort key None-mixing, F8 discord interaction.data None): not in my FIXES list, not touched.
5. Other concurrently-modified files in the working tree belong to sibling executors (E2a/E2b/E3/E4/E5 test files present); I did not touch anything outside my claim.
