# Wave 2 — E7 Execution Report (Orchestrator vision + tool fallthrough)

Agent: E7 · Date: 2026-08-25 · Claim: `gateway/orchestrator.py` (tool-loop, vision session flow, chat error paths) + `gateway/vision.py`

## Fixes applied (all verified first against source)

### 1. L3#4 MEDIUM — unregistered advertised tools no longer shell-executed
`orchestrator.py` chat() tool loop: computed `_advertised_names` from `native_schemas`; when `fn_name not in tool_registry.tools`, dotted names (`browser.*`, `desktop.*`, `mcp.*`) or any name that appears in the advertised schema set now returns a clear tool-error result ("Tool error: 'X' is not available in this session.") instead of falling through to `executor.execute(fn_name, fn_args)` with an args dict bound as `cwd`. Plain non-dotted unregistered names keep the legacy shell fallback (legit shell-command tool calls preserved).

### 2. L3#5 MEDIUM — malformed tool-args JSON surfaced
`orchestrator.py` chat() tool loop: JSON parse failure now debug-logs a truncated repr of the bad args and appends a note into the tool result fed back to the model: "(note: your arguments were invalid JSON and were reset to {}; retry the call with valid JSON)".

### 3. L4#2 CRITICAL-class — vision provider errors never silent
`handle_gateway_event` CHAT branch: image-bearing `self.chat(...)` call wrapped in try/except; on failure sends user-visible fallback: "I couldn't analyze that image with the current model (<error, 200-char cap>). Try /models to switch to a vision-capable model." plus a warning log.

### 4. L4#5 MEDIUM — oversized image payloads not persisted
New module constant `_MAX_PERSISTED_IMAGE_URL_CHARS = 4 MiB`. Data URLs exceeding it are still used for THIS turn's analysis but are not written to `session["last_image_url"]`, so multi-MB base64 blobs stop being re-serialized into LanceDB `session_json` on every turn. Tradeoff documented inline at the write site (follow-up reattach lost for oversized images — accepted).

### 5. L4#4 MEDIUM — VisionBridge bounded, marked unused-but-retained
Chose retention over deletion/deletion-wiring (minimal risk). Docstring now clearly marks the class unused-by-default with rationale. Added `MAX_CACHE_FILES = 50` and best-effort `_sweep_cache()` (mtime-sorted, newest-kept) invoked after each cache write; sweep failures degrade to debug log.

### 6. L4#1 partial — dropped image context now observable
Follow-up heuristic else-branch: debug log added when prior image context is cleared due to no keyword match (frequency visible in morning review).

## Tests

`tests/python/unit/test_nightshift_wave2_e7.py` — 4 tests:
- `test_unregistered_dotted_tool_not_shell_executed` (executor.execute monkeypatched to raise if called)
- `test_malformed_tool_args_note_returned_to_model`
- `test_vision_provider_error_sends_visible_fallback`
- `test_oversized_image_not_persisted_in_session`

Results:
- Targeted file: **4 passed** in ~4s
- Regression sweep `test_nightshift_wave2_e7.py + tests/python/unit -k "orchestr or vision or tool"`: **74 passed, 659 deselected** in ~100s

## Deferred (out of claim / for Wave 3)

- F1/F2 full scope: attachments channel into ConversationCore; per-model `images_in` capability flag.
- F5 full scope: pre-download Telegram 20MiB cap (`photo.file_size`) lives in `tg_client.py` (not claimed); out-of-band image storage still open.
- F6 remainder: multimodal conversation memory (history rows store text only), non-English follow-up triggers.
- F7: Google/Gemini inline_data drop; Copilot Responses historical-image loss; llama.cpp local vision capacity unused.
- L3#5 sibling site `direct_loop.py:186-190` (same malformed-args coercion) — outside claim (owned by direct-loop decoupling work); tolerant extraction via `extract_json_object()` not wired here.
