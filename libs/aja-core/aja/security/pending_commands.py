"""Per-command exec approval store with TTL + one-shot tokens.

Complements mission-level approvals (gateway/approvals.py) with a lightweight
in-memory pending-command flow for Telegram-originated shell commands:

- CommandGuard classifies a command as "ask" -> PendingCommandStore.create()
  returns a one-shot token; the adapter sends inline ✅/❌ buttons carrying
  ``execok_<token>`` / ``execno_<token>`` callback data.
- Owner taps -> resolve() under a per-token lock. One-shot: the token is
  terminal after the first resolution (idempotent against double-taps).
- Expiry: ISO stamp, default TTL 300s; unparseable stamps are fail-closed
  (treated expired), matching approvals.py semantics.
- TOCTOU guard: resolve() does NOT authorize execution by itself — callers
  MUST re-run CommandGuard.classify_command on the exact byte-string at
  execution time. Approval clears an "ask" verdict; it can NEVER clear a
  "deny" verdict.

Decisions are journaled to aja_runtime_events (EXEC_REQUESTED / EXEC_APPROVED /
EXEC_REJECTED / EXEC_EXPIRED) via secretary.add_runtime_event when available;
journal failures never block the flow.
"""

import asyncio
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300

# Per-token in-process claim locks (same pattern as approvals._mission_locks):
# setdefault is GIL-atomic and contains no await point; a module-level
# asyncio.Lock would bind to whichever loop first awaited it.
_token_locks: Dict[str, asyncio.Lock] = {}


def _token_lock(token: str) -> asyncio.Lock:
    return _token_locks.setdefault(token, asyncio.Lock())


@dataclass
class PendingCommand:
    command: str
    chat_id: str
    user_id: str
    token: str
    created_at: datetime
    expires_at: datetime
    resolved: bool = False
    approved: Optional[bool] = None

    def expired(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        # Fail-closed on unparseable/naive-less stamps is handled at creation;
        # here expires_at is always tz-aware.
        return now >= self.expires_at


class PendingCommandStore:
    """In-process pending-command registry. Not persisted across restarts:
    a gateway restart invalidates pending approvals, which is the safe
    failure mode for execution authorization."""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._pending: Dict[str, PendingCommand] = {}

    def create(
        self,
        command: str,
        chat_id: str,
        user_id: str,
        ttl_seconds: Optional[int] = None,
    ) -> PendingCommand:
        # Sweep expired entries opportunistically so the dict cannot grow
        # unbounded across a long-lived gateway process.
        self._sweep()
        now = datetime.now(timezone.utc)
        effective_ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        pc = PendingCommand(
            command=command,
            chat_id=str(chat_id),
            user_id=str(user_id),
            token=secrets.token_hex(8),
            created_at=now,
            expires_at=now + timedelta(seconds=effective_ttl),
        )
        self._pending[pc.token] = pc
        self._journal("EXEC_REQUESTED", pc, f"Approval requested: {command}")
        return pc

    def get(self, token: str) -> Optional[PendingCommand]:
        pc = self._pending.get(token)
        if pc is None:
            return None
        if pc.expired():
            return None
        return pc

    async def resolve(
        self, token: str, approved: bool, user_id: str
    ) -> Tuple[bool, str]:
        """Resolve a pending command one-shot. Returns (handled, message).

        Idempotency + expiry checks run INSIDE the per-token lock so double
        taps / concurrent redeliveries produce exactly one side effect.
        """
        async with _token_lock(token):
            pc = self._pending.get(token)
            if pc is None:
                return False, "No pending approval found for that request."
            if pc.resolved:
                verdict = "approved" if pc.approved else "rejected"
                return False, f"Already {verdict}."
            if pc.expired():
                pc.resolved = True
                pc.approved = False
                self._journal("EXEC_EXPIRED", pc, "Approval expired before resolution")
                return False, "That approval request expired."

            pc.resolved = True
            pc.approved = approved
            kind = "EXEC_APPROVED" if approved else "EXEC_REJECTED"
            self._journal(kind, pc, f"Command {'approved' if approved else 'rejected'} by {user_id}")
            verdict = "approved" if approved else "rejected"
            return True, f"Command {verdict}."

    def _sweep(self) -> None:
        now = datetime.now(timezone.utc)
        dead = [tok for tok, pc in self._pending.items() if pc.expired(now)]
        for tok in dead:
            self._pending.pop(tok, None)
            _token_locks.pop(tok, None)

    def _journal(self, kind: str, pc: PendingCommand, message: str) -> None:
        self.journal_event(kind, pc.command, pc.token, message)

    def journal_event(
        self,
        kind: str,
        command: str,
        target: str,
        message: str,
        status: str = "SUCCESS",
    ) -> None:
        try:
            from aja.memory.secretary import get_aja_memory

            row = {
                "event_id": uuid.uuid4().hex[:8],
                "kind": kind,
                "target": target,
                "status": status,
                "message": message,
                "command": command,
                "metadata_json": "{}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            get_aja_memory().add_runtime_event(row)
        except Exception as e:
            logger.debug("exec-approval journal write skipped: %s", e)



_default_store: Optional[PendingCommandStore] = None


def get_pending_command_store() -> PendingCommandStore:
    global _default_store
    if _default_store is None:
        _default_store = PendingCommandStore()
    return _default_store


def reset_pending_command_store() -> None:
    """Test hook: drop the singleton and its locks."""
    global _default_store
    _default_store = None
    _token_locks.clear()
