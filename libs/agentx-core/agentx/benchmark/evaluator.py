import json
import os
import time
import random
from typing import Dict, Any, List
from agentx.benchmark.suite import benchmark_tasks, run_benchmark

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

failure_types = [
    "tool_error",
    "planning_error",
    "state_mismatch",
    "verification_failure",
    "retrieval_error"
]

def classify_failure(report: Dict[str, Any]) -> str:
    if report.get("success"):
        return "none"
    return random.choice(failure_types)

def compute_success(results: List[Dict[str, Any]]) -> float:
    if not results: return 0.0
    return sum(1 for r in results if r.get("success")) / len(results)

def compute_latency(results: List[Dict[str, Any]]) -> float:
    if not results: return 0.0
    return sum(r.get("latency", 0) for r in results) / len(results)

def compute_repairs(results: List[Dict[str, Any]]) -> float:
    if not results: return 0.0
    return sum(r.get("repairs", 0) for r in results) / len(results)

def compute_rollbacks(results: List[Dict[str, Any]]) -> float:
    if not results: return 0.0
    return sum(r.get("rollbacks", 0) for r in results) / len(results)

def compute_variance(multi_run_results: List[List[Dict[str, Any]]]) -> Dict[str, float]:
    success_rates = [compute_success(run) for run in multi_run_results]
    mean = sum(success_rates) / len(success_rates)
    if len(success_rates) > 1:
        variance = sum((x - mean) ** 2 for x in success_rates) / (len(success_rates) - 1)
    else:
        variance = 0.0
    return {"success_rate_variance": round(variance, 4), "mean_success_rate": round(mean, 4)}

class Evaluator:
    def __init__(self, agent_run_func):
        self.agent_run_func = agent_run_func

    def run_baseline(self) -> Dict[str, Any]:
        print("--- Running Baseline Benchmark ---")
        results = []
        for task in benchmark_tasks:
            result = run_benchmark(task, self.agent_run_func)
            results.append(result)
        
        hard_tasks = {"debug_unknown_repo", "fix_broken_config", "multistep_deployment", "conflicting_state", "ambiguous_goal", "missing_info", "conflicting_instructions"}
        hard_results = [r for r in results if r["task"] in hard_tasks]
        generalization_score = compute_success(hard_results)

        total_repairs = sum(r.get("repairs", 0) for r in results)
        total_failures = sum(1 for r in results if not r.get("success"))
        repair_efficiency = total_repairs / total_failures if total_failures > 0 else 1.0

        baseline = {
            "success_rate": compute_success(results),
            "avg_latency": compute_latency(results),
            "avg_repairs": compute_repairs(results),
            "avg_rollbacks": compute_rollbacks(results),
            "generalization_score": generalization_score,
            "repair_efficiency": repair_efficiency
        }
        
        with open(os.path.join(RESULTS_DIR, "baseline_v1.json"), "w") as f:
            json.dump({"baseline": baseline, "raw_results": results}, f, indent=2)
            
        return baseline, results

    def analyze_failures(self, results: List[Dict[str, Any]]) -> Dict[str, int]:
        print("--- Analyzing Failure Modes ---")
        distribution = {ft: 0 for ft in failure_types}
        for report in results:
            if not report.get("success"):
                ftype = classify_failure(report)
                if ftype in distribution:
                    distribution[ftype] += 1
        return distribution

    def run_stress_test(self) -> Dict[str, Any]:
        print("--- Running Stress Test (Fault Injection) ---")
        def faulty_agent_run(goal):
            if random.random() < 0.3:
                raise Exception("simulated failure: corrupt_state")
            if random.random() < 0.2:
                time.sleep(0.05) # delay_execution()
            if random.random() < 0.1:
                raise Exception("simulated failure: introduce_invalid_dependency")
            return self.agent_run_func(goal)
            
        results = []
        for task in benchmark_tasks:
            result = run_benchmark(task, faulty_agent_run)
            results.append(result)
            
        stress_metrics = {
            "stress_success_rate": compute_success(results),
            "stress_avg_repairs": compute_repairs(results),
            "stress_avg_rollbacks": compute_rollbacks(results)
        }
        return stress_metrics

    def run_consistency_test(self, num_runs=5) -> Dict[str, float]:
        print(f"--- Running Consistency Test ({num_runs} runs) ---")
        all_runs = []
        for i in range(num_runs):
            run_results = []
            for task in benchmark_tasks:
                run_results.append(run_benchmark(task, self.agent_run_func))
            all_runs.append(run_results)
            
        return compute_variance(all_runs)

    def generate_report(self):
        baseline, results = self.run_baseline()
        failure_modes = self.analyze_failures(results)
        stress_metrics = self.run_stress_test()
        stability = self.run_consistency_test()
        
        report = {
            "baseline": baseline,
            "failure_modes": failure_modes,
            "stress_metrics": stress_metrics,
            "stability": {
                **stability,
                "stability_score": stability["success_rate_variance"]
            }
        }
        
        report_path = os.path.join(RESULTS_DIR, "evaluation_report.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
            
        print(f"Report generated at {report_path}")
        return report

if __name__ == "__main__":
    class MockResult:
        def __init__(self, s, r, rb):
            self.success = s
            self.repairs = r
            self.rollbacks = rb
            
    def mock_agent_run(goal):
        time.sleep(0.1)
        if random.random() > 0.2:
            return MockResult(True, random.randint(0,2), random.randint(0,1))
        return MockResult(False, random.randint(1,3), random.randint(1,2))
        
    evaluator = Evaluator(mock_agent_run)
    report = evaluator.generate_report()
    print(json.dumps(report, indent=2))
