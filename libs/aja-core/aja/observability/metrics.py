import threading
from typing import Dict, Any

class MetricsSystem:
    """Collects real-time and historical execution metrics.

    Thread-safe: EventBus callbacks may fire from any thread; all mutations
    are serialized behind a lock so increments cannot be lost.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.metrics = {
            "total_nodes_executed": 0,
            "success_count": 0,
            "failure_count": 0,
            "rollback_count": 0,
            "repair_success_count": 0,
            "total_latency_sec": 0.0,
            "beta_plans_generated": 0,
            "beta_total_diversity": 0.0,
            "beta_total_variance": 0.0,
            "beta_total_latency": 0.0,
            "stable_plans_generated": 0,
            "stable_total_latency": 0.0,
            "beta_success": 0,
            "stable_success": 0,
        }

    def record_success(self, latency: float = 0.0):
        with self._lock:
            self.metrics["total_nodes_executed"] += 1
            self.metrics["success_count"] += 1
            self.metrics["total_latency_sec"] += latency

            import aja.config
            if getattr(aja.config, "AJA_DIVERSITY_BETA", False):
                self.metrics["beta_success"] += 1
            else:
                self.metrics["stable_success"] += 1

    def record_failure(self):
        with self._lock:
            self.metrics["total_nodes_executed"] += 1
            self.metrics["failure_count"] += 1

    def record_rollback(self):
        with self._lock:
            self.metrics["rollback_count"] += 1

    def record_repair(self):
        with self._lock:
            self.metrics["repair_success_count"] += 1

    def record_beta_metrics(self, data: Dict[str, Any]):
        with self._lock:
            self.metrics["beta_plans_generated"] += 1
            self.metrics["beta_total_diversity"] += data.get("diversity_score", 0.0)
            self.metrics["beta_total_variance"] += data.get("plan_variance", 0.0)
            self.metrics["beta_total_latency"] += data.get("latency", 0.0)

    def record_stable_metrics(self, latency: float):
        with self._lock:
            self.metrics["stable_plans_generated"] += 1
            self.metrics["stable_total_latency"] += latency

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            m = dict(self.metrics)

        total = max(m["total_nodes_executed"], 1)

        beta_count = max(m["beta_plans_generated"], 1)
        stable_count = max(m["stable_plans_generated"], 1)
        beta_avg_lat = m["beta_total_latency"] / beta_count
        stable_avg_lat = m["stable_total_latency"] / stable_count

        return {
            "success_rate": m["success_count"] / total,
            "rollback_count": m["rollback_count"],
            "repair_rate": m["repair_success_count"] / max(m["failure_count"], 1),
            "avg_latency": m["total_latency_sec"] / total,
            "diversity_score": m["beta_total_diversity"] / beta_count,
            "plan_variance": m["beta_total_variance"] / beta_count,
            "success_rate_beta": m["beta_success"] / max(m["beta_success"] + m["failure_count"], 1), # Approx
            "success_rate_stable": m["stable_success"] / max(m["stable_success"] + m["failure_count"], 1), # Approx
            "latency_increase": beta_avg_lat - stable_avg_lat
        }

metrics_system = MetricsSystem()

# We can wire this up to EventBus as well
from aja.runtime.event_bus import bus, EVENTS
bus.subscribe_once(EVENTS["NODE_SUCCESS"], lambda n: metrics_system.record_success(), "metrics:NODE_SUCCESS")
bus.subscribe_once(EVENTS["NODE_FAILED"], lambda n: metrics_system.record_failure(), "metrics:NODE_FAILED")
bus.subscribe_once(EVENTS["ROLLBACK"], lambda n: metrics_system.record_rollback(), "metrics:ROLLBACK")
bus.subscribe_once(EVENTS["REPAIR"], lambda n: metrics_system.record_repair(), "metrics:REPAIR")
