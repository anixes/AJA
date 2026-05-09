from typing import Dict, Any

class MetricsSystem:
    """Collects real-time and historical execution metrics."""
    
    def __init__(self):
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
        self.metrics["total_nodes_executed"] += 1
        self.metrics["success_count"] += 1
        self.metrics["total_latency_sec"] += latency
        
        import agentx.config
        if getattr(agentx.config, "AGENTX_DIVERSITY_BETA", False):
            self.metrics["beta_success"] += 1
        else:
            self.metrics["stable_success"] += 1

    def record_failure(self):
        self.metrics["total_nodes_executed"] += 1
        self.metrics["failure_count"] += 1

    def record_rollback(self):
        self.metrics["rollback_count"] += 1

    def record_repair(self):
        self.metrics["repair_success_count"] += 1

    def record_beta_metrics(self, data: Dict[str, Any]):
        self.metrics["beta_plans_generated"] += 1
        self.metrics["beta_total_diversity"] += data.get("diversity_score", 0.0)
        self.metrics["beta_total_variance"] += data.get("plan_variance", 0.0)
        self.metrics["beta_total_latency"] += data.get("latency", 0.0)

    def record_stable_metrics(self, latency: float):
        self.metrics["stable_plans_generated"] += 1
        self.metrics["stable_total_latency"] += latency

    def get_summary(self) -> Dict[str, Any]:
        total = max(self.metrics["total_nodes_executed"], 1)
        
        beta_count = max(self.metrics["beta_plans_generated"], 1)
        stable_count = max(self.metrics["stable_plans_generated"], 1)
        beta_avg_lat = self.metrics["beta_total_latency"] / beta_count
        stable_avg_lat = self.metrics["stable_total_latency"] / stable_count
        
        return {
            "success_rate": self.metrics["success_count"] / total,
            "rollback_count": self.metrics["rollback_count"],
            "repair_rate": self.metrics["repair_success_count"] / max(self.metrics["failure_count"], 1),
            "avg_latency": self.metrics["total_latency_sec"] / total,
            "diversity_score": self.metrics["beta_total_diversity"] / beta_count,
            "plan_variance": self.metrics["beta_total_variance"] / beta_count,
            "success_rate_beta": self.metrics["beta_success"] / max(self.metrics["beta_success"] + self.metrics["failure_count"], 1), # Approx
            "success_rate_stable": self.metrics["stable_success"] / max(self.metrics["stable_success"] + self.metrics["failure_count"], 1), # Approx
            "latency_increase": beta_avg_lat - stable_avg_lat
        }

metrics_system = MetricsSystem()

# We can wire this up to EventBus as well
from agentx.runtime.event_bus import bus, EVENTS
bus.subscribe(EVENTS["NODE_SUCCESS"], lambda n: metrics_system.record_success())
bus.subscribe(EVENTS["NODE_FAILED"], lambda n: metrics_system.record_failure())
bus.subscribe(EVENTS["ROLLBACK"], lambda n: metrics_system.record_rollback())
bus.subscribe(EVENTS["REPAIR"], lambda n: metrics_system.record_repair())
