# Wave 2 — Executor E4 (Streaming) — Ledger

**Agent**: E4 · **Claim**: `tg_client.py`, `adapters/telegram_envelope.py`, `render.py`
**Brief**: `.opencode/night-shift/briefs/wave-2/G1.md` (F1, F2, F4 + task items 5/6)
**Status**: ✅ COMPLETE — all fixes implemented and regression-tested.

## Fix 1 — HIGH — Final replies >4096 silently dropped (`tg_client.py`)

* **Was**: `send_message()` caught every send exception → returned None. Any orchestrator
  response >4096 chars hit Telegram's TEXT_TOO_LONG and the user got nothing while history
  still recorded the reply.
* **Now**: new module-level `split_for_telegram(text, limit=4096)` splits on newline/space
  boundaries (hard cut fallback at column 4096). `send_message()` sends each chunk; markup
  (`reply_markup`) rides only on the final chunk so buttons aren't duplicated. Per-chunk
  failures are logged/metric'd instead of aborting the rest.
* Verified: chunks ≤4096; concatenation lossless for boundary/no-boundary cases.

## Fix 2 — HIGH — `_handle_stream_chunk` had zero exception handling (`telegram_envelope.py`)

Rewrote with a single error-tolerant delivery path:

* **RetryAfter (429)**: detected via `exc.retry_after` attr (typed PTB error, no hard import);
  sleeps the requested seconds exactly once, retries the same op once, resets the failure
  counter on success. A second consecutive failure falls into normal error accounting.
* **Terminal errors**: `"message is not modified"` / `"message to edit not found"` finalize
  (prune) the stream state so the next chunk starts a fresh message instead of editing a ghost.
* **Circuit breaker**: per-key consecutive-error counter; at 3 consecutive failures it sends a
  fresh final message with the full truncated buffer, prunes state, and stops editing.
  Fallback-send failure also prunes. Non-terminal failures consume the throttle window
  (back-off spacing).

## Fix 3 — MEDIUM-HIGH — No 4096 cap on streamed buffers

`_cap_stream_text()` clamps any send/edit payload to 4096 chars with an ` …[truncated]`
marker. The monotonic-growth buffer can no longer poison every subsequent edit with
TEXT_TOO_LONG (the F1×F2 interaction).

## Fix 4 — MEDIUM — Stream-state leak + cross-turn bleed

* States now carry `created_at` (injectable `_clock`) and `consecutive_edit_errors`.
* TTL sweep: `_prune_stale_stream_states()` runs per chunk (TTL 1800 s default,
  `STREAM_STATE_TTL_SECONDS`); aged states are dropped so a later turn starts fresh.
* Terminal finalization (not-modified / not-found / breaker trip / stop()) pops the key.
* Correlation: key remains `correlation_id or chat_id`; correlation-less manual envelopes
  sharing a chat_id now recover correctly because terminal/TTL pruning clears stale previews.
  Regression test pins that two distinct correlation_ids in one chat never cross-edit.

## Fix 5 — MEDIUM — Photo download getFile cap (`tg_client.py`)

Photos with `file_size > 20 MiB` are rejected before `get_file()`; a user-visible message
("Image skipped … too large … >20 MB limit") is sent and text-only processing continues.
Normal-size downloads verified unchanged.

## Fix 6 — render.py / parse_mode on streaming edits — VERIFIED, NO CHANGE NEEDED

Audited both live call sites:
* Live path `TelegramAdapter.send_message` renders via `MobileMDRenderer` with
  `parse_mode=None` (plain text) — no MarkdownV2 anywhere.
* The only streaming-edit path (`_handle_stream_chunk`) already calls `bot.send_message` /
  `bot.edit_message_text` with **no** parse_mode; envelope `render()`'s MarkdownV2 auto-mode
  is only reachable via non-stream `send_envelope` (G1-F6 territory, not claimed here).
Pinned with `test_stream_edits_stay_plain_text_no_parse_mode` so a future refactor can't
silently reintroduce mid-stream parse-entity 400s.

## Files touched

| File | Change |
|---|---|
| `libs/aja-core/aja/gateway/tg_client.py` | `split_for_telegram()` + split-and-send in `send_message`; 20 MiB photo guard |
| `libs/aja-core/aja/gateway/adapters/telegram_envelope.py` | Hardened `_handle_stream_chunk` (RetryAfter / terminal errors / breaker / 4096 cap), TTL pruning, constants |
| `tests/python/unit/test_nightshift_wave2_e4.py` | NEW — 19 tests |

No changes to `render.py` (see Fix 6).

## Test results

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e4.py -q --timeout=300 -p no:cacheprovider
→ 19 passed in 6.27s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e4.py tests/python/unit \
  -k "stream or telegram or render or chunk" -q --timeout=300 -p no:cacheprovider
→ 76 passed, 712 deselected in 24.78s   (incl. pre-existing test_telegram_envelope.py)
```

Coverage of required scenarios: 4096 splitting ✓, circuit breaker ✓, state pruning (terminal
+ TTL) ✓, RetryAfter ✓, oversized-photo notice ✓, plain-text stream edits ✓, cross-turn bleed ✓.

## Notes for next agents

* G1-F3 (chat_stream duplicated fallback), F5 (discord_envelope dead streaming path), F6
  (envelope render auto-MarkdownV2) remain open — outside my claim.
* The envelope adapter is still dormant migration code (nothing emits STREAM_CHUNK in prod);
  these fixes make it wiring-safe. AGENTS.md Phase-15 "circuit breaker" claim is now true.
