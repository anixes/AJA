# Exec Approvals Spec — Per-Command Telegram Inline Approvals

Research date: 2026-08-26. Status: READY TO IMPLEMENT.

## Verified findings

### approvals.py pattern (to clone)
- `resolve_approval(platform, user_id, mission_id, action="approve") -> (bool, str)`
- Locking: module-level `_mission_locks: Dict[str, asyncio.Lock]` keyed by id,
  `setdefault` (GIL-atomic). NOT a module-level asyncio.Lock (loop-binding bug).
  Terminal-status re-check INSIDE the lock closes in-process TOCTOU.
- Expiry: ISO stamp in metadata (`approval_expires_at`), `_parse_expiry`
  (naive=UTC; unparseable=fail-closed expired).
- Idempotency: terminal statuses {ACTIVE,DONE,FAILED,REJECTED}; second tap
  returns (False, "already handled").
- Persistence: LanceDB missions via get_aja_memory(); journal rows to
  aja_runtime_events {event_id(8hex), kind, target, status, message,
  command:"", metadata_json, timestamp}. NODE_APPROVED/REJECTED.
- Rollback restores prior status if transition/journal write throws.

### tg_client.py
- `_handle_callback` (~L546): requires data.startswith(("approve_","reject_")),
  splits action_mission_id. Auth: exact from_user.id match vs allowlist.

## Spec

New file `libs/aja-core/aja/security/pending_commands.py`:

```python
class PendingCommandStore:
    async def create(command: str, chat_id, user_id, ttl_s=300) -> str  # token
    async def get(token: str) -> Optional[PendingCommand]  # checks expiry
    async def resolve(token: str, approved: bool, user_id) -> (bool, str)
    # per-token asyncio.Lock via setdefault; one-shot (terminal after resolve);
    # expiry sweep on access + periodic task
```

Callback data format: `execok_<token>` / `execno_<token>` (extend
_handle_callback's startswith tuple).

Flow:
1. /pc or natural-language path wants shell command → CommandGuard.classify
2. deny → reply reason + journal EXEC_DENIED. allow → run immediately.
3. ask → store.create() → send inline keyboard ✅/❌ with command text shown
4. Owner taps → _handle_callback → PendingCommandStore.resolve()
   - RE-CLASSIFY the exact byte-string at execution time (TOCTOU guard);
     approval clears "ask" but NEVER "deny" (re-deny even if approved earlier)
5. Execute via ToolExecutor → result to chat + journal EXEC_COMPLETED(exit_code)

Journal kinds: EXEC_REQUESTED / EXEC_APPROVED / EXEC_REJECTED /
EXEC_COMPLETED / EXEC_DENIED. Include chat_id/user_id/command hash.

Security invariants:
- Deny-list never overridable by approval
- One-shot tokens, TTL default 300s
- Byte-match: the string executed must equal the string classified at resolve time
- redact_secrets on all outbound text

Test gates: existing approvals tests must not regress
(test_nightshift_wave1_e3.py RepairRecord tests, callback routing tests).
New: create/resolve/expiry/idempotency/deny-override-attempt/unit tests +
callback integration test through _handle_callback with fake update.

Effort: ~1 day.
