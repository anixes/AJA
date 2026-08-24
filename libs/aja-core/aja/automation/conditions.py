"""Pre-fire condition evaluation for scheduled jobs.

Evaluates declarative condition dicts against live system state before a
CronScheduler job is allowed to fire. All conditions are ANDed together.

Supported condition types:
    {"type": "expression", "expr": "error_count < 5"}
    {"type": "llm_check", "prompt": "Is now a good time to...?"}
    {"type": "file_exists", "path": "/tmp/signal.txt"}

Expression safety: expressions are parsed with ``ast`` and evaluated against
a whitelist of node types only — no ``eval()``, no attribute access, no
function calls. Names resolve from the state dict.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import operator
import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = ["ConditionResult", "ConditionEvaluator"]


_CMP_OPS = {
    ast.Lt: operator.lt,
    ast.Gt: operator.gt,
    ast.LtE: operator.le,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
}

_BOOL_OPS = {ast.And: all, ast.Or: any}

_MAX_EXPR_LEN = 512


@dataclass
class ConditionResult:
    """Outcome of a pre-fire condition evaluation."""

    met: bool
    reason: str = ""


class _SafeExprEvaluator(ast.NodeVisitor):
    """Minimal AST walker supporting comparisons, and/or/not, and state lookups."""

    def __init__(self, state: dict):
        self.state = state

    def visit_Expression(self, node: ast.expr) -> Any:
        return self.visit(node.body)

    def visit_Compare(self, node: ast.Compare) -> bool:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            fn = _CMP_OPS.get(type(op))
            if fn is None:
                raise ValueError(f"unsupported comparison operator: {type(op).__name__}")
            right = self.visit(comparator)
            if not fn(left, right):
                return False
            left = right
        return True

    def visit_BoolOp(self, node: ast.BoolOp) -> bool:
        fn = _BOOL_OPS.get(type(node.op))
        if fn is None:
            raise ValueError(f"unsupported boolean operator: {type(node.op).__name__}")
        return fn(bool(self.visit(v)) for v in node.values)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        if isinstance(node.op, ast.Not):
            return not bool(self.visit(node.operand))
        if isinstance(node.op, ast.USub):
            return -self.visit(node.operand)
        raise ValueError(f"unsupported unary operator: {type(node.op).__name__}")

    def visit_Name(self, node: ast.Name) -> Any:
        if node.id not in self.state:
            raise KeyError(f"unknown state variable: {node.id!r}")
        return self.state[node.id]

    def visit_Constant(self, node: ast.Constant) -> Any:
        if isinstance(node.value, (bool, int, float, str)) or node.value is None:
            return node.value
        raise ValueError(f"unsupported constant: {node.value!r}")

    def generic_visit(self, node: ast.AST) -> Any:
        raise ValueError(f"disallowed expression element: {type(node).__name__}")


def _evaluate_expression(expr: str, state: dict) -> bool:
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError("expression must be a non-empty string")
    if len(expr) > _MAX_EXPR_LEN:
        raise ValueError("expression exceeds maximum length")
    tree = ast.parse(expr, mode="eval")
    return bool(_SafeExprEvaluator(state).visit(tree))


def _parse_yes_no(text: str) -> bool:
    lowered = text.strip().lower()
    head = lowered.splitlines()[0] if lowered else ""
    for token in ("yes", "y,", "y.", "true"):
        if head.startswith(token):
            return True
    return False


class ConditionEvaluator:
    """Evaluates pre-fire conditions against system state."""

    def __init__(
        self,
        state_provider: Optional[Callable[[], dict]] = None,
        gateway: Any = None,
    ):
        """Args:
        state_provider: returns a dict of system metrics/state.
        gateway: optional LLMGateway used by ``llm_check`` conditions.
        """
        self.state_provider = state_provider
        self.gateway = gateway

    def evaluate(self, conditions: list[dict]) -> ConditionResult:
        """Evaluate a list of condition dicts; ALL must be met (AND).

        On any evaluation error, returns met=False carrying the error message.
        """
        if conditions is None:
            return ConditionResult(met=True, reason="no conditions configured")

        try:
            state = dict(self.state_provider()) if self.state_provider else {}
        except Exception as exc:
            logger.warning("condition state provider failed: %s", exc)
            return ConditionResult(met=False, reason=f"state provider error: {exc}")

        for cond in conditions:
            try:
                result = self._evaluate_one(cond, state)
            except Exception as exc:
                ctype = (cond or {}).get("type", "?")
                logger.warning("condition %s failed: %s", ctype, exc)
                return ConditionResult(met=False, reason=f"{ctype} error: {exc}")
            if not result.met:
                return result
        return ConditionResult(met=True, reason=f"all {len(conditions)} conditions met")

    def _evaluate_one(self, cond: dict, state: dict) -> ConditionResult:
        if not isinstance(cond, dict):
            raise ValueError("condition must be a dict")
        ctype = cond.get("type")

        if ctype == "expression":
            ok = _evaluate_expression(cond.get("expr"), state)
            return ConditionResult(
                met=ok,
                reason=f"expression '{cond.get('expr')}' {'met' if ok else 'not met'}",
            )

        if ctype == "llm_check":
            prompt = cond.get("prompt")
            if not prompt:
                raise ValueError("llm_check requires 'prompt'")
            if self.gateway is None:
                return ConditionResult(met=False, reason="no gateway for LLM check")
            answer = self._ask_llm(str(prompt))
            return ConditionResult(met=answer, reason=f"llm_check answered {'yes' if answer else 'no'}")

        if ctype == "file_exists":
            path = cond.get("path")
            if not path or not isinstance(path, str):
                raise ValueError("file_exists requires a non-empty 'path'")
            expanded = os.path.expandvars(os.path.expanduser(path))
            exists = os.path.isfile(expanded)
            return ConditionResult(
                met=exists,
                reason=f"file '{expanded}' {'exists' if exists else 'not found'}",
            )

        return ConditionResult(met=False, reason=f"unknown condition type: {ctype!r}")

    def _ask_llm(self, prompt: str) -> bool:
        completion = getattr(self.gateway, "llm", self.gateway).completion
        outcome = completion(prompt + "\nAnswer strictly YES or NO.")
        if inspect.isawaitable(outcome):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RuntimeError("async gateway completion cannot be awaited inside a running event loop")
            outcome = asyncio.run(outcome)
        text = str(outcome or "")
        return _parse_yes_no(text)
