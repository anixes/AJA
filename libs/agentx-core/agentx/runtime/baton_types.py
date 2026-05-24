import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


MISSION_BATON_FIELDS: Tuple[str, ...] = (
    "objective",
    "run_id",
    "history_json",
    "metadata_json",
)

WORKER_BATON_FIELDS: Tuple[str, ...] = (
    "objective",
    "status",
    "stage",
    "worker_stdout",
    "error",
    "payload",
)


@dataclass(frozen=True)
class MissionBatonPayload:
    """
    Python contract for the Rust write_baton/read_baton Arrow schema.
    """

    objective: str
    run_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_native_args(self) -> Tuple[str, str, str, str]:
        return (
            self.objective,
            self.run_id,
            json.dumps(self.history),
            json.dumps(self.metadata),
        )

    def to_state(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "run_id": self.run_id,
            "history": self.history,
            "metadata": self.metadata,
        }

    @classmethod
    def from_state(cls, objective: str, state: Dict[str, Any]) -> "MissionBatonPayload":
        return cls(
            objective=objective,
            run_id=state.get("run_id", "unknown"),
            history=list(state.get("history", [])),
            metadata=dict(state.get("metadata", {})),
        )

    @classmethod
    def from_native_dict(cls, data: Dict[str, Any]) -> "MissionBatonPayload":
        return cls(
            objective=data.get("objective", ""),
            run_id=data.get("run_id", ""),
            history=json.loads(data.get("history_json") or "[]"),
            metadata=json.loads(data.get("metadata_json") or "{}"),
        )


@dataclass(frozen=True)
class WorkerBatonPayload:
    """
    Python contract for the Rust write_baton_ipc/read_baton_ipc Arrow schema.
    """

    data: Dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.data)

    @classmethod
    def from_json(cls, payload: str) -> "WorkerBatonPayload":
        return cls(json.loads(payload))
