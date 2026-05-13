import json
import uuid
import lancedb
import pyarrow as pa
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Dict
from agentx.memory.manager import list_tables_defensive

class WorkerRegistry:
    """
    Worker Registry for Agent Swarm.
    Tracks worker performance, specialties, and availability using LanceDB.
    """
    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / ".agentx" / "lancedb"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))
        self._init_table()

    def _init_table(self):
        schema = pa.schema([
            ("worker_id", pa.string()),
            ("name", pa.string()),
            ("specialty", pa.string()), # Comma-separated list
            ("reliability", pa.float32()),
            ("latency", pa.float32()),
            ("cost_index", pa.float32()),
            ("vram_available_mb", pa.int32()),
            ("cpu_load", pa.float32()),
            ("status", pa.string()),
            ("last_seen", pa.string()),
            ("metadata_json", pa.string())
        ])
        existing = list_tables_defensive(self.db)
            
        if "worker_registry" not in existing:
            self.db.create_table("worker_registry", schema=schema)

    def register_worker(self, worker_id: str, name: str, specialty: List[str]):
        table = self.db.open_table("worker_registry")
        now = datetime.utcnow().isoformat() + "Z"
        
        # Check if exists
        existing = table.search().where(f"worker_id = '{worker_id}'").to_list()
        if existing:
            table.update(where=f"worker_id = '{worker_id}'", values={
                "name": name,
                "specialty": ",".join(specialty),
                "last_seen": now
            })
        else:
            table.add([{
                "worker_id": worker_id,
                "name": name,
                "specialty": ",".join(specialty),
                "reliability": 1.0,
                "latency": 0.0,
                "cost_index": 0.5,
                "status": "idle",
                "last_seen": now,
                "metadata_json": "{}"
            }])

    def update_metrics(self, worker_id: str, success: bool, latency: float, telemetry: Optional[Dict] = None):
        table = self.db.open_table("worker_registry")
        worker = table.search().where(f"worker_id = '{worker_id}'").to_list()
        if not worker:
            return
        
        w = worker[0]
        # Exponential moving average for reliability
        alpha = 0.2
        new_reliability = (w["reliability"] * (1 - alpha)) + (1.0 if success else 0.0) * alpha
        new_latency = (w["latency"] * (1 - alpha)) + latency * alpha
        
        updates = {
            "reliability": new_reliability,
            "latency": new_latency,
            "last_seen": datetime.utcnow().isoformat() + "Z"
        }
        
        if telemetry:
            updates["vram_available_mb"] = telemetry.get("vram_available", 0)
            updates["cpu_load"] = telemetry.get("cpu_load", 0.0)
            
        table.update(where=f"worker_id = '{worker_id}'", values=updates)

    def get_best_worker(self, required_specialty: str = None) -> Optional[Dict[str, Any]]:
        table = self.db.open_table("worker_registry")
        query = table.search()
        if required_specialty:
            # Simple substring match in specialty string
            query = query.where(f"specialty LIKE '%{required_specialty}%'")
        
        # Sort by reliability DESC, latency ASC
        results = query.to_list()
        if not results:
            return None
            
        # Manual sort since LanceDB scalar sort is limited in some versions
        results.sort(key=lambda x: (x["reliability"], -x["latency"]), reverse=True)
        return results[0]

    def list_workers(self) -> List[Dict[str, Any]]:
        return self.db.open_table("worker_registry").search().to_list()
