# Bridge.py Split — Phase 3 Spec (AppContext + extractions)

Research date: 2026-08-26. Status: READY TO IMPLEMENT.

## Current state
api/bridge.py = 2781 lines. services/ has priority_engine, worker_matcher,
command_policy, legacy_dashboard.

## Line-range map
- Lifespan/startup L48-80; config constants L103-126 (RUNTIME_STATE_PATH,
  BATON_DIR, API_TOKEN, TELEGRAM_* x5, AJA_MEMORY_DIR)
- Policy tables L128-155; auth helpers L158-177
- WS manager L188-245 (stays in bridge — FastAPI-bound)
- Persistence helpers L274-404
- Telegram formatting/router L410-658, L850-926, L1293-1440, L2309-2375
- Approval workflow L932-1290 (~350 lines)
- Runtime state/status L1672-1850, L2377-2460
- Config persistence L2589-2635; trigger rate limiter L2638-2651
- Telegram polling loop L2693-2767

## Extraction conventions (from priority_engine.py)
Module docstring "Extracted from api/bridge.py..."; pure functions only, no
FastAPI imports; memory via parameter; bridge re-imports # noqa: E402,F401.
NOTE: place re-exports near TOP of bridge (existing mid-file block is legacy).

## AppContext (aja/api/app_context.py)
@dataclass: api_token, default_api_token, telegram_* (token/allowed_user/
webhook_secret/command_timeout), data_dir + all path fields (history/pending/
audit/runtime_state/baton/config), memory_provider callable, ws_manager,
approval_claim_locks dict, trigger_counters dict.
Lifecycle: module-level lazy singleton get_app_context(); from_env() reads env
ONCE (preserve import-time semantics some tests rely on); tests override via
monkeypatch on the module. Passed explicitly as first arg to service fns.

## Migration order (dependency-driven)
1. config_store.py (lowest coupling ~50 lines, zero risk)
2. approval_service.py (~350 lines; needs ctx + injected send_message callable)
3. telegram_gateway.py last (depends on approvals + messaging; poller takes ctx
   + on_message callback to avoid importing bridge)
Target end state: bridge ~1200-1400 lines (routes + wiring).

## Test gates
test_aja_hardening.py (bridge.analyze_shell_command), test_fleet_loop.py,
test_mobile_bridge_websocket.py (app/API_TOKEN/ws),
test_runtime_boundaries.py (READ FULLY FIRST — AST layering checks may
constrain new modules), test_nightshift_wave2_e2.py, test_webhook_trigger.py
(mutates bridge._trigger_counters — update fixture in same commit).
Isolation tests forbid aja.api.bridge in conversation subprocess imports —
new services must not be pulled in by those paths.

## Risks
- Circular: telegram↔approvals → callback injection or shared messaging.py
- Import-time env reads: preserve read-once semantics or env-monkeypatch tests break
- verify_token is a FastAPI Depends in many routes — keep importable from bridge
- One-way dependency: services ← bridge only (uvicorn deadlock otherwise)

Suggested first session: steps 1-2 (AppContext + config_store) ≈ half day.
