import time
from typing import Dict, Any, List
import json

class BenchmarkTask:
    def __init__(self, name: str, goal: str, expected_outcome: str):
        self.name = name
        self.goal = goal
        self.expected_outcome = expected_outcome

benchmark_tasks = [
    # SYSTEM TASKS
    BenchmarkTask(
        "deploy_project",
        "clone repo -> install deps -> run",
        "project runs successfully"
    ),

    # DEBUG TASKS
    BenchmarkTask(
        "fix_failing_tests",
        "analyze test failure -> fix code",
        "tests pass"
    ),

    # AUTOMATION TASKS
    BenchmarkTask(
        "setup_env",
        "install packages -> configure system",
        "env ready"
    ),

    # HARD TASKS
    BenchmarkTask(
        "debug_unknown_repo",
        "debug unknown repo with missing dependencies",
        "missing dependencies identified and installed"
    ),
    BenchmarkTask(
        "fix_broken_config",
        "fix broken system config with partial logs",
        "system configuration repaired"
    ),
    BenchmarkTask(
        "multistep_deployment",
        "multi-step deployment with failure injection",
        "deployment succeeds after recovery"
    ),
    BenchmarkTask(
        "conflicting_state",
        "conflicting state updates across nodes",
        "state conflicts resolved"
    ),

    # ADVERSARIAL TASKS
    BenchmarkTask(
        "ambiguous_goal",
        "ambiguous goal",
        "agent requests clarification or makes safe assumptions"
    ),
    BenchmarkTask(
        "missing_info",
        "missing information",
        "agent discovers missing info safely"
    ),
    BenchmarkTask(
        "conflicting_instructions",
        "conflicting instructions",
        "agent resolves conflict safely"
    )
]

metrics = {
    "success_rate": 0,
    "avg_latency": 0,
    "rollback_count": 0,
    "repair_count": 0,
    "token_usage": 0
}

def run_benchmark(task: BenchmarkTask, agent_run_func) -> Dict[str, Any]:
    """Runs a single benchmark task and captures metrics."""
    start_time = time.time()
    
    # Mocking actual run execution for metrics gathering.
    # agent_run_func must return a result object containing success, repairs, rollbacks
    try:
        result = agent_run_func(task.goal)
        success = getattr(result, "success", False)
        repairs = getattr(result, "repairs", 0)
        rollbacks = getattr(result, "rollbacks", 0)
    except Exception as e:
        print(f"Error running task {task.name}: {e}")
        success = False
        repairs = 0
        rollbacks = 0
    
    latency = time.time() - start_time
    
    report = {
        "task": task.name,
        "success": success,
        "repairs": repairs,
        "rollbacks": rollbacks,
        "latency": round(latency, 2)
    }
    return report

def run_all_benchmarks(agent_run_func) -> List[Dict[str, Any]]:
    reports = []
    for task in benchmark_tasks:
        reports.append(run_benchmark(task, agent_run_func))
        
    print(json.dumps(reports, indent=2))
    return reports
