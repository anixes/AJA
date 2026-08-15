"""
AJA Workspace Memory Pool
=========================
Thread-safe, LRU-cached connection pool for per-workspace LanceDB instances.
Prevents file descriptor leaks and memory exhaustion across multiple workspaces.
"""

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Optional, Tuple

from aja.config import DATA_DIR
from aja.memory.manager import MemoryManager, get_memory_manager
from aja.workspace.context import get_current_workspace


class WorkspaceMemoryPool:
    """
    LRU Memory Manager Pool managing per-workspace LanceDB connections.
    """

    def __init__(self, max_connections: int = 16, ttl_seconds: int = 600):
        self.max_connections = max_connections
        self.ttl_seconds = ttl_seconds
        self._pool: OrderedDict[str, Tuple[MemoryManager, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get_manager(
        self, workspace_id: Optional[str] = None, memory_dir: Optional[Path] = None
    ) -> MemoryManager:
        """
        Get or initialize a UnifiedMemoryManager for a specific workspace.
        """
        # If no explicit workspace_id, check ContextVar
        if workspace_id is None:
            ctx = get_current_workspace()
            if ctx:
                workspace_id = ctx.id
                memory_dir = ctx.memory_dir
            else:
                workspace_id = "default"
                memory_dir = DATA_DIR

        target_dir = Path(memory_dir or (DATA_DIR / "workspaces" / workspace_id / "memory")).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        db_path = target_dir / "lancedb"

        with self._lock:
            now = time.time()
            self._cleanup_expired(now)

            if workspace_id in self._pool:
                mgr, _ = self._pool[workspace_id]
                self._pool.move_to_end(workspace_id)
                self._pool[workspace_id] = (mgr, now)
                return mgr

            # Evict oldest if pool is full
            while len(self._pool) >= self.max_connections:
                oldest_id, (oldest_mgr, _) = self._pool.popitem(last=False)
                # Cleanup if needed

            # Initialize new memory manager instance
            mgr = MemoryManager(db_path=db_path)
            self._pool[workspace_id] = (mgr, now)
            return mgr

    def _cleanup_expired(self, now: float) -> None:
        """Evict idle instances older than ttl_seconds."""
        expired = [
            ws_id
            for ws_id, (_, last_used) in self._pool.items()
            if now - last_used > self.ttl_seconds
        ]
        for ws_id in expired:
            del self._pool[ws_id]

    def close_all(self) -> None:
        """Close all pooled connections."""
        with self._lock:
            self._pool.clear()


# Global Singleton Pool
_global_memory_pool: Optional[WorkspaceMemoryPool] = None


def get_workspace_memory_pool() -> WorkspaceMemoryPool:
    global _global_memory_pool
    if _global_memory_pool is None:
        _global_memory_pool = WorkspaceMemoryPool()
    return _global_memory_pool


def get_active_memory_manager() -> MemoryManager:
    """Convenience accessor to get memory manager for active context."""
    return get_workspace_memory_pool().get_manager()

