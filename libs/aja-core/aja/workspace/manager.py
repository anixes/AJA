"""
AJA Workspace Registry Manager
==============================
Manages workspace definitions, persistence in workspaces.json,
dynamic path resolution, and storage partitioning.
"""

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from aja.workspace.context import WorkspaceContext


@dataclass
class Workspace:
    """
    Representation of a registered project workspace in the AJA Agent OS.
    """
    id: str
    name: str
    path: str
    created_at: str
    config_overrides: Dict[str, Any] = field(default_factory=dict)
    active: bool = False

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).resolve()


class WorkspaceRegistry:
    """
    Centralized registry for managing multi-project workspaces.
    Persists data in ~/.aja/workspaces.json or $AJA_DATA_DIR/workspaces.json.
    """

    def __init__(self, storage_root: Optional[Path] = None):
        if storage_root is not None:
            self.storage_root = Path(storage_root).resolve()
        else:
            try:
                from aja.config import DATA_DIR
                self.storage_root = DATA_DIR.resolve()
            except Exception:
                self.storage_root = Path(os.environ.get("AJA_DATA_DIR") or (Path.home() / ".aja")).resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.storage_root / "workspaces.json"
        self._workspaces_dir = self.storage_root / "workspaces"
        self._workspaces_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        self.workspaces: Dict[str, Workspace] = {}
        if self.registry_file.exists():
            try:
                data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                for ws_id, ws_data in data.items():
                    self.workspaces[ws_id] = Workspace(
                        id=ws_data["id"],
                        name=ws_data["name"],
                        path=ws_data["path"],
                        created_at=ws_data.get("created_at", ""),
                        config_overrides=ws_data.get("config_overrides", {}),
                        active=ws_data.get("active", False),
                    )
            except Exception:
                self.workspaces = {}

    def _save(self) -> None:
        data = {ws.id: asdict(ws) for ws in self.workspaces.values()}
        self.registry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @staticmethod
    def _sanitize_name(name: str) -> str:
        s = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower())
        return re.sub(r"-+", "-", s).strip("-") or "workspace"

    def add(
        self,
        path: Path | str,
        name: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
        set_active: bool = False,
    ) -> Workspace:
        """Register a new workspace or update existing path registration."""
        resolved_path = Path(path).resolve()
        if not resolved_path.exists():
            raise FileNotFoundError(f"Workspace path does not exist: {resolved_path}")

        # Check if already registered by path
        for ws in self.workspaces.values():
            if Path(ws.path).resolve() == resolved_path:
                if name:
                    ws.name = self._sanitize_name(name)
                if config_overrides is not None:
                    ws.config_overrides = config_overrides
                if set_active:
                    self.set_active(ws.id)
                self._save()
                return ws

        ws_id = str(uuid.uuid4())[:8]
        ws_name = self._sanitize_name(name or resolved_path.name)
        
        # Ensure name uniqueness
        existing_names = {w.name for w in self.workspaces.values()}
        base_name = ws_name
        counter = 1
        while ws_name in existing_names:
            ws_name = f"{base_name}-{counter}"
            counter += 1

        import time
        created_at = time.strftime("%Y-%m-%d %H:%M:%S")

        is_first = len(self.workspaces) == 0
        should_activate = set_active or is_first

        if should_activate:
            for w in self.workspaces.values():
                w.active = False

        workspace = Workspace(
            id=ws_id,
            name=ws_name,
            path=str(resolved_path),
            created_at=created_at,
            config_overrides=config_overrides or {},
            active=should_activate,
        )

        self.workspaces[ws_id] = workspace
        self._save()
        return workspace

    def get(self, name_or_id: str) -> Optional[Workspace]:
        """Find a workspace by ID or by name."""
        if not name_or_id:
            return None
        name_or_id_clean = name_or_id.strip()
        
        # Match by ID
        if name_or_id_clean in self.workspaces:
            return self.workspaces[name_or_id_clean]

        # Match by name (case-insensitive)
        for ws in self.workspaces.values():
            if ws.name.lower() == name_or_id_clean.lower():
                return ws

        return None

    def get_or_default(self, name_or_id: Optional[str] = None) -> Workspace:
        """Get workspace by name/id, or active workspace, or fallback to current project."""
        if name_or_id:
            ws = self.get(name_or_id)
            if ws:
                return ws

        # Try active workspace
        active = self.get_active()
        if active and active.resolved_path.exists():
            return active

        # Fallback to current project root
        return self.get_default_workspace()

    def get_active(self) -> Optional[Workspace]:
        """Get the currently active workspace."""
        for ws in self.workspaces.values():
            if ws.active and ws.resolved_path.exists():
                return ws
        # If no active flag or path invalid, return first valid one
        for ws in self.workspaces.values():
            if ws.resolved_path.exists():
                ws.active = True
                self._save()
                return ws
        return None

    def set_active(self, name_or_id: str) -> bool:
        """Set active workspace by name or ID."""
        target = self.get(name_or_id)
        if not target:
            return False

        for ws in self.workspaces.values():
            ws.active = (ws.id == target.id)

        self._save()
        return True

    def remove(self, name_or_id: str) -> bool:
        """Unregister a workspace."""
        target = self.get(name_or_id)
        if not target:
            return False

        del self.workspaces[target.id]
        if target.active and self.workspaces:
            next(iter(self.workspaces.values())).active = True

        self._save()
        return True

    def list_all(self) -> List[Workspace]:
        """Return all registered workspaces."""
        return list(self.workspaces.values())

    def get_default_workspace(self) -> Workspace:
        """Create or return the default workspace pointing to PROJECT_ROOT."""
        try:
            from aja.config import PROJECT_ROOT
            target_root = PROJECT_ROOT.resolve()
        except Exception:
            target_root = Path.cwd().resolve()
        for ws in self.workspaces.values():
            if ws.resolved_path == target_root:
                return ws

        return self.add(
            path=target_root,
            name="default",
            set_active=True,
        )

    def create_context(self, workspace: Workspace) -> WorkspaceContext:
        """Construct isolated WorkspaceContext for execution."""
        ws_storage = self._workspaces_dir / workspace.id
        ws_storage.mkdir(parents=True, exist_ok=True)
        return WorkspaceContext(
            id=workspace.id,
            name=workspace.name,
            path=workspace.resolved_path,
            storage_dir=ws_storage,
            config_overrides=workspace.config_overrides,
        )


# Global singleton registry instance
_default_registry: Optional[WorkspaceRegistry] = None


def get_workspace_registry() -> WorkspaceRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = WorkspaceRegistry()
    return _default_registry
