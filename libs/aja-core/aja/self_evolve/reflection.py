import json
import logging
import time
import uuid
from typing import Dict, Any, List

import lancedb
import pyarrow as pa
import aja.config
from aja.llm import get_gateway_for_model
from aja.runtime.event_bus import bus, EVENTS
from aja.memory.manager import list_tables_defensive
from aja.memory.secretary import sanitize_value

logger = logging.getLogger(__name__)

KNOWLEDGE_DB_DIR = aja.config.PROJECT_ROOT / ".aja" / "lancedb"
KNOWLEDGE_TABLE = "self_evolve_knowledge"


class KnowledgeBase:
    def __init__(self):
        self.db = None
        self.table = None
        self._init_lancedb()

    def _init_lancedb(self):
        KNOWLEDGE_DB_DIR.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(KNOWLEDGE_DB_DIR))
        if KNOWLEDGE_TABLE not in list_tables_defensive(self.db):
            schema = pa.schema(
                [
                    ("entry_id", pa.string()),
                    ("kind", pa.string()),
                    ("key", pa.string()),
                    ("payload_json", pa.string()),
                    ("created_at", pa.float64()),
                ]
            )
            self.db.create_table(KNOWLEDGE_TABLE, schema=schema)
        self.table = self.db.open_table(KNOWLEDGE_TABLE)

    def _add_entry(self, kind: str, key: str, payload: Dict[str, Any]):
        if self.table is None:
            raise RuntimeError("Self-evolve knowledge table is unavailable.")
        self.table.add(
            [
                {
                    "entry_id": uuid.uuid4().hex,
                    "kind": kind,
                    "key": key,
                    "payload_json": json.dumps(payload),
                    "created_at": time.time(),
                }
            ]
        )

    def add_pattern(self, pattern: Dict[str, Any]):
        p_id = pattern.get("pattern")
        if not p_id:
            return

        # Query for the latest count for this pattern
        current_count = 0
        try:
            results = (
                self.table.search()
                .where(f"kind = 'pattern' AND key = {sanitize_value(p_id)}")
                .limit(1)
                .to_list()
            )
            if results:
                payload = json.loads(results[0].get("payload_json") or "{}")
                current_count = payload.get("count", 0)
        except Exception as e:
            logger.warning(f"Failed to query pattern frequency: {e}")

        new_count = current_count + 1
        self._add_entry("pattern", p_id, {"pattern": pattern, "count": new_count})

        # Part C - Auto Tool Creation (Controlled)
        if new_count == 3:  # Threshold
            print(
                f"[SelfEvolve] Pattern '{p_id}' crossed threshold ({new_count}). Proposing capability."
            )
            from aja.self_build.capability_builder import self_build_cycle

            # Trigger self-build using the pattern description as the problem
            self_build_cycle(f"Automate workflow: {p_id}")

    def add_reflection(self, problem: str, reflection: Dict[str, Any]):
        self._add_entry(
            "reflection", problem, {"problem": problem, "reflection": reflection}
        )

    @property
    def best_patterns(self) -> List[Dict[str, Any]]:
        """
        Dynamically fetches the top patterns from the database.
        Maintains O(1) in-memory state while providing O(N_limit) access.
        """
        if self.table is None:
            return []
        try:
            # Fetch recent patterns
            rows = (
                self.table.search()
                .where("kind = 'pattern'")
                .limit(20)
                .to_list()
            )
            unique_patterns = {}
            for row in rows:
                payload = json.loads(row.get("payload_json") or "{}")
                p = payload.get("pattern")
                if p and p.get("pattern") not in unique_patterns:
                    unique_patterns[p.get("pattern")] = p
            return list(unique_patterns.values())[:5]
        except Exception:
            return []

    def load(self):
        """Deprecated: KnowledgeBase now uses on-demand queries."""
        pass


knowledge_base = KnowledgeBase()


def evaluate_postconditions(result: Dict[str, Any]) -> Dict[str, Any]:
    checks = []

    def add(name: str, passed: bool, detail: str):
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    if "success" in result:
        add("reported_success", bool(result.get("success")), "Result success flag.")
    if "ok" in result:
        add("reported_ok", bool(result.get("ok")), "Result ok flag.")
    if "code" in result or "exit_code" in result:
        code = result.get("code", result.get("exit_code"))
        add("exit_code_zero", code == 0, f"Exit code: {code}")
    if "compile_ok" in result:
        add(
            "compile_ok",
            bool(result.get("compile_ok")),
            "Compile check supplied by executor.",
        )
    if "tests_passed" in result:
        add(
            "tests_passed",
            bool(result.get("tests_passed")),
            "Test check supplied by executor.",
        )
    if "files_changed" in result:
        changed = result.get("files_changed") or []
        add("files_changed", len(changed) > 0, f"Changed files: {len(changed)}")
    if "changed_files" in result:
        changed = result.get("changed_files") or []
        add("changed_files", len(changed) > 0, f"Changed files: {len(changed)}")

    failed = [check for check in checks if not check["passed"]]
    return {
        "checks": checks,
        "passed": not failed,
        "failed": failed,
        "summary": "; ".join(
            f"{c['name']}={'pass' if c['passed'] else 'fail'}" for c in checks
        )
        or "No deterministic checks supplied.",
    }


def reflect(goal: str, plan: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Part A - Reflection Engine
    """
    model_name = aja.config.AJA_PLANNER_MODEL
    postconditions = result.get("postconditions") or evaluate_postconditions(result)
    if postconditions["failed"]:
        return {
            "success": False,
            "what_worked": "",
            "what_failed": "Deterministic postconditions failed: "
            + postconditions["summary"],
            "bottlenecks": "Skipped LLM reflection because deterministic checks failed.",
            "optimization_opportunities": "Fix failing postconditions before semantic reflection.",
            "postconditions": postconditions,
        }

    # Serialize plan for LLM
    plan_desc = ""
    if hasattr(plan, "nodes"):
        plan_desc = ", ".join([getattr(n, "task", "step") for n in plan.nodes])
    elif isinstance(plan, list):
        plan_desc = ", ".join([str(n) for n in plan])
    else:
        plan_desc = str(plan)

    system = """You are the Agent Reflection Engine.
Analyze the executed goal, the plan, and its result.
Return ONLY JSON:
{
    "success": true/false,
    "what_worked": "...",
    "what_failed": "...",
    "bottlenecks": "...",
    "optimization_opportunities": "..."
}
"""
    prompt = f"Goal: {goal}\nPlan: {plan_desc}\nResult: {json.dumps(result)}\nPostconditions: {json.dumps(postconditions)}\n\nReflect on this execution:"
    try:
        from aja.llm import completion
        raw = completion(prompt=prompt, system_prompt=system, model=model_name)
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        reflection = json.loads(raw)
        reflection["postconditions"] = postconditions
        return reflection
    except Exception as e:
        print(f"[Reflection] Error: {e}")
        return {
            "success": result.get("success", False),
            "what_worked": "",
            "what_failed": str(e),
            "bottlenecks": "",
            "optimization_opportunities": "",
            "postconditions": postconditions,
        }


def extract_pattern(goal: str, plan: Any) -> Dict[str, Any]:
    """
    Part B - Pattern Extraction
    """
    plan_desc = []
    if hasattr(plan, "nodes"):
        plan_desc = [getattr(n, "task", "step") for n in plan.nodes]
    elif isinstance(plan, list):
        plan_desc = [str(n) for n in plan]

    return {
        "pattern": f"workflow_for_{goal.replace(' ', '_')[:20]}",
        "steps": plan_desc,
        "tools": [],  # Extracted tools could go here
    }


def process_execution(goal: str, plan: Any, result: Dict[str, Any]):
    """
    Part F & H - Improvement Trigger & Feedback Loop
    Run after each execution
    """
    print(f"[SelfEvolve] Running reflection and pattern extraction for: {goal}")
    result["postconditions"] = result.get("postconditions") or evaluate_postconditions(
        result
    )

    # Reflect
    reflection = reflect(goal, plan, result)

    # Extract Pattern
    pattern = extract_pattern(goal, plan)

    # Store Knowledge
    knowledge_base.add_reflection(goal, reflection)
    knowledge_base.add_pattern(pattern)

    # Part D - Self-Optimization
    from aja.rl.policy_store import policy_store

    if reflection.get("bottlenecks"):
        print(f"[SelfEvolve] Identified bottlenecks: {reflection['bottlenecks']}")
        # adjust latency / scoring
        # example logic to simulate optimization
        pass

    if not reflection.get("success", True):
        print("[SelfEvolve] Failure detected in reflection. Triggering policy adjustment.")
        policy_store.exploration_rate = min(1.0, policy_store.exploration_rate + 0.1)
