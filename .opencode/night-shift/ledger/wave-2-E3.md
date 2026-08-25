# Wave 2 — E3 Recovery Ledger (Approvals Pipeline)

Executor: E3 (recovery — prior agent died mid-task) · Date: 2026-08-25
Claim honored (exclusive): `gateway/approvals.py` · `core/conversation.py` (resolve_approval + attachment regions only) · `gateway/adapters/discord_envelope.py` · `messaging/envelope.py` (not needed)

## STEP 1 inventory (prior agent's partial edits)

| Region | State found |
|---|---|
| approvals.py expiry parse (`_parse_expiry`, fail-closed) | ✅ done, correct |
| approvals.py rollback + try/except around both transitions | ✅ done, correct |
| approvals.py atomic claim (`_mission_locks` + `_locks_guard`) | ⚠️ done but **broken** (see fix 1) |
| conversation.py resolve_approval keyword alignment | ✅ done, correct |
| conversation.py image-attachment notice | ✅ done, correct (`Final` already imported via events wildcard import) |
| discord_envelope.py `view=None` on both edit paths | ✅ done, correct |
| test_nightshift_wave2_e3.py | existed unverified; 17/17 green after fix 1 |
| Mission #4 (expiry written at emission) | ❌ not started — all emission sites outside claim → documented below |

## Fixes applied

### Fix 1 — cross-event-loop deadlock in the atomic claim (approvals.py)
The dead agent's `_mission_locks` used a module-level `_locks_guard = asyncio.Lock()`. A module-level asyncio.Lock binds to whichever event loop first awaits it; under anyio every test gets a fresh loop, and any multi-loop process would raise `RuntimeError: ... bound to a different event loop` from the second loop onward.
**Change**: removed the guard. `dict.setdefault(mission_id, asyncio.Lock())` is atomic under the GIL with no await between check-and-set, so the guard bought nothing. Per-mission locks are still created lazily inside the running loop and held across status flip AND side effects; terminal-status re-check inside the lock closes the TOCTOU window in-process. Cross-process callers remain guarded by the terminal-status check only (LanceDB offers no CAS) — comment updated to match reality.

## Verification of inherited edits (no changes needed)
- **#1 CRITICAL**: reject now reaches the right mission with `approved=False`; engine called as `(platform="cli", user_id=approver_id, mission_id=approval_id, action=...)`. Regression-tested via spy-engine + end-to-end fake memory (status REJECTED, journal target == mission id).
- **#2 HIGH**: naive stamps assumed UTC; garbage/non-string expiry → fail-closed expired, zero journal writes, no TypeError escape.
- **#3 HIGH**: concurrent double-approve resolves exactly one side effect (laggy `update_mission` widens race; exactly one ACTIVE transition, one NODE_APPROVED); sequential second click → "already handled"; failed journal write rolls status back to pre-transition value.
- **#5 MEDIUM**: resolution AND unauthorized-click edits both pass `view=None` (buttons detach).
- **#6 MEDIUM**: image attachments yield user-visible Final naming the files; LLM pipeline not invoked; non-image messages flow unchanged.

## Test results

```
py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e3.py -q --timeout=300 -p no:cacheprovider
→ 17 passed in 7.48s

py -3.12 -m pytest tests/python/unit/test_nightshift_wave2_e3.py tests/python/unit \
  -k "approval or conversation or resolve" -q --timeout=300 -p no:cacheprovider \
  --ignore=tests/python/unit/test_nightshift_wave2_e2.py
→ 45 passed, 763 deselected in 581.36s (0:09:41)
```

## ⚠️ Blocker for coordinator — NOT E3's scope (E2's file)

The mandated sweep WITHOUT `--ignore` cannot go green on this tree:
`tests/python/unit/test_nightshift_wave2_e2.py` **hangs the whole pytest process after its first test prints PASSED** (`test_protected_routes_reject_anonymous[/runtime/approvals]`), reproducible when the file is run completely alone; the per-test timeout never fires and the earlier full run also recorded a failure inside that file before stalling. Verified NOT caused by E3: E2's tests exercise bridge HTTP routes only; `api/` never imports `aja.gateway.approvals`, and none of my claimed files are in its import path. Hand back to E2/coordinator.

Minor observation (pre-existing, out of claim): `test_secretary_fixes.py::test_cleanup_old_approvals_*` each spent ~286s in *setup* before passing normally in the big sweep — smells like leftover hung processes/LanceDB lock contention from the aborted runs above; worth a look if suite times inflate.

## Mission #4 documentation — emission sites (all OUTSIDE my claim; nobody had them modified at time of writing)

1. **`goals/goal_engine.py:244-252` (`escalate_to_user`) — PRIMARY writer needed.**
   Today writes metadata `{message, mission_id}` + `update_mission(status="AWAITING_APPROVAL")`. Proposed one-liner inside the metadata dict:
   ```python
   "approval_expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
   ```
   (TTL value is a product decision — 24h suggested; brief F4 confirms this is the only mission-side writer.)
2. **`planning/react_executor.py:161,225`** — publishes AWAITING_APPROVAL bus payloads without `mission_id` or expiry → adapters render buttons targeting `"system"` that always dead-end. Needs mission_id threading + same stamp before keyboards render.
3. **`api/bridge.py:938-960`** — tool-command approval path already writes real `expiresAt` into `aja_approvals`; no action.
4. Reader side (`approvals.py:116-128`) honors `approval_expires_at` OR legacy `expires_at`, fail-closed — ready as soon as writers exist.

Note: `messaging/envelope.py` required no changes (Attachment/InboundMessage shapes already matched the notice implementation).

— E3 out.
