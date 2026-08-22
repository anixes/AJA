import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class SessionReplayDataLoader:
    """
    Artifact hydration layer that parses execution directories
    and reconstructs timeline events and state for replay visualization.

    Corrupt artifacts are logged loudly (replay-authoritative system must not
    silently render empty timelines) and surfaced via ``corruption_warnings``.
    """
    def __init__(self, session_id: str, executions_dir: Path):
        self.session_id = session_id
        self.session_dir = executions_dir / session_id
        self.manifest: Optional[Dict[str, Any]] = None
        self.result: Optional[Dict[str, Any]] = None
        self.timeline: List[Dict[str, Any]] = []
        self.workspace_diff: Optional[Dict[str, Any]] = None
        self.corruption_warnings: List[str] = []

        self.load_data()

    def _load_json(self, path: Path, attr: str) -> None:
        try:
            setattr(self, attr, json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            warning = f"Corrupt {path.name} in session {self.session_id}: {e}"
            self.corruption_warnings.append(warning)
            logger.warning(warning)

    def load_data(self):
        if not self.session_dir.exists():
            raise ValueError(f"Session directory not found: {self.session_dir}")

        manifest_path = self.session_dir / "manifest.json"
        if manifest_path.exists():
            self._load_json(manifest_path, "manifest")

        result_path = self.session_dir / "result.json"
        if result_path.exists():
            self._load_json(result_path, "result")

        timeline_path = self.session_dir / "timeline.jsonl"
        if timeline_path.exists():
            try:
                for line in timeline_path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        self.timeline.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        # Torn final line from a crash mid-append is expected;
                        # mid-file corruption is more concerning but tolerated.
                        warning = f"Skipping corrupt timeline line in {timeline_path.name}: {e}"
                        self.corruption_warnings.append(warning)
                        logger.warning(warning)
            except OSError as e:
                warning = f"Could not read timeline for session {self.session_id}: {e}"
                self.corruption_warnings.append(warning)
                logger.warning(warning)

        # Ensure chronological ordering by sequence number; events missing a
        # sequence sort after sequenced ones instead of jumping to the front.
        self.timeline.sort(key=lambda e: (isinstance(e.get("sequence"), int), e.get("sequence", 0)))

        diff_path = self.session_dir / "workspace_diff.json"
        if diff_path.exists():
            self._load_json(diff_path, "workspace_diff")

    def get_events(self) -> List[Dict[str, Any]]:
        return self.timeline

    def get_manifest(self) -> Optional[Dict[str, Any]]:
        return self.manifest

    def get_result(self) -> Optional[Dict[str, Any]]:
        return self.result

    def get_diff(self) -> Optional[Dict[str, Any]]:
        return self.workspace_diff
