import json
from pathlib import Path
from typing import Dict, Any, List, Optional


class SessionReplayDataLoader:
    """
    Artifact hydration layer that parses execution directories
    and reconstructs timeline events and state for replay visualization.
    """
    def __init__(self, session_id: str, executions_dir: Path):
        self.session_id = session_id
        self.session_dir = executions_dir / session_id
        self.manifest: Optional[Dict[str, Any]] = None
        self.result: Optional[Dict[str, Any]] = None
        self.timeline: List[Dict[str, Any]] = []
        self.workspace_diff: Optional[Dict[str, Any]] = None
        
        self.load_data()

    def load_data(self):
        if not self.session_dir.exists():
            raise ValueError(f"Session directory not found: {self.session_dir}")

        manifest_path = self.session_dir / "manifest.json"
        if manifest_path.exists():
            try:
                self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        result_path = self.session_dir / "result.json"
        if result_path.exists():
            try:
                self.result = json.loads(result_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        timeline_path = self.session_dir / "timeline.jsonl"
        if timeline_path.exists():
            try:
                for line in timeline_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self.timeline.append(json.loads(line))
            except Exception:
                pass
        
        # Ensure chronological ordering by sequence number
        self.timeline.sort(key=lambda e: e.get("sequence", 0))

        diff_path = self.session_dir / "workspace_diff.json"
        if diff_path.exists():
            try:
                self.workspace_diff = json.loads(diff_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def get_events(self) -> List[Dict[str, Any]]:
        return self.timeline

    def get_manifest(self) -> Optional[Dict[str, Any]]:
        return self.manifest

    def get_result(self) -> Optional[Dict[str, Any]]:
        return self.result
        
    def get_diff(self) -> Optional[Dict[str, Any]]:
        return self.workspace_diff
