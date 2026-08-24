"""Unit tests for aja.automation.conditions.ConditionEvaluator."""

import asyncio

import pytest

from aja.automation.conditions import ConditionEvaluator, ConditionResult


class _FakeGateway:
    def __init__(self, reply="YES"):
        self.reply = reply
        self.prompts = []

    def ask(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def _gateway(reply="YES"):
    gw = _FakeGateway(reply)
    wrapper = type("G", (), {})()
    wrapper.llm = type("LLM", (), {"completion": staticmethod(lambda p: gw.ask(p))})()
    wrapper._fake = gw
    return wrapper


class TestExpression:
    def test_simple_comparison_true(self):
        ev = ConditionEvaluator(state_provider=lambda: {"cpu_percent": 45})
        result = ev.evaluate([{"type": "expression", "expr": "cpu_percent < 80"}])
        assert isinstance(result, ConditionResult)
        assert result.met is True

    def test_simple_comparison_false(self):
        ev = ConditionEvaluator(state_provider=lambda: {"cpu_percent": 95})
        result = ev.evaluate([{"type": "expression", "expr": "cpu_percent < 80"}])
        assert result.met is False

    def test_all_supported_operators(self):
        state = {"a": 1, "b": 2, "s": "x"}
        ev = ConditionEvaluator(state_provider=lambda: state)
        for expr in (
            "a < b",
            "b >= 2",
            "a <= 1",
            "b > 1",
            "a == 1",
            "a != 2",
            's == "x"',
            "not (a > b)",
            "a < b and b == 2",
            "a > 5 or b == 2",
            "-a < 0",
        ):
            assert ev.evaluate([{"type": "expression", "expr": expr}]).met is True, expr

    def test_unknown_state_variable_is_error(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "expression", "expr": "ghost < 5"}])
        assert result.met is False
        assert "unknown state variable" in result.reason

    def test_dangerous_constructs_rejected(self):
        ev = ConditionEvaluator(state_provider=lambda: {"x": 1})
        for expr in (
            "__import__('os').system('ls')",
            "(lambda: x)()",
            "[y for y in range(3)]",
            "x.__class__",
        ):
            result = ev.evaluate([{"type": "expression", "expr": expr}])
            assert result.met is False
            assert "error" in result.reason.lower()


class TestAndSemantics:
    def test_multiple_conditions_anded(self):
        state = {"cpu_percent": 45, "error_count": 2}
        ev = ConditionEvaluator(state_provider=lambda: state)
        result = ev.evaluate(
            [
                {"type": "expression", "expr": "cpu_percent < 80"},
                {"type": "expression", "expr": "error_count < 5"},
            ]
        )
        assert result.met is True

    def test_second_condition_fails(self):
        state = {"cpu_percent": 45, "error_count": 10}
        ev = ConditionEvaluator(state_provider=lambda: state)
        result = ev.evaluate(
            [
                {"type": "expression", "expr": "cpu_percent < 80"},
                {"type": "expression", "expr": "error_count < 5"},
            ]
        )
        assert result.met is False
        assert "error_count" in result.reason

    def test_empty_list_met(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        assert ev.evaluate([]).met is True


class TestFileExists:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "signal.txt"
        f.write_text("go")
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "file_exists", "path": str(f)}])
        assert result.met is True
        assert "conditions met" in result.reason

    def test_missing_file(self, tmp_path):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "file_exists", "path": str(tmp_path / "nope.txt")}])
        assert result.met is False
        assert "not found" in result.reason

    def test_directory_is_not_file(self, tmp_path):
        ev = ConditionEvaluator(state_provider=lambda: {})
        assert ev.evaluate([{"type": "file_exists", "path": str(tmp_path)}]).met is False

    def test_missing_path_key_is_error(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "file_exists"}])
        assert result.met is False
        assert "error" in result.reason.lower()


class TestLLMCheck:
    def test_no_gateway_skips_with_false(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "llm_check", "prompt": "Is now a good time?"}])
        assert result.met is False
        assert result.reason == "no gateway for LLM check"

    def test_gateway_yes(self):
        ev = ConditionEvaluator(state_provider=lambda: {}, gateway=_gateway("YES"))
        assert ev.evaluate([{"type": "llm_check", "prompt": "Go?"}]).met is True

    def test_gateway_no(self):
        ev = ConditionEvaluator(state_provider=lambda: {}, gateway=_gateway("NO"))
        assert ev.evaluate([{"type": "llm_check", "prompt": "Go?"}]).met is False

    def test_gateway_exception_becomes_error_result(self):
        class Boom:
            class llm:
                @staticmethod
                def completion(prompt):
                    raise RuntimeError("provider down")

        ev = ConditionEvaluator(state_provider=lambda: {}, gateway=Boom())
        result = ev.evaluate([{"type": "llm_check", "prompt": "Go?"}])
        assert result.met is False
        assert "provider down" in result.reason

    def test_async_gateway_outside_loop_resolves(self):
        class AsyncGateway:
            class llm:
                @staticmethod
                async def completion(prompt):
                    return "YES"

        ev = ConditionEvaluator(state_provider=lambda: {}, gateway=AsyncGateway())
        assert ev.evaluate([{"type": "llm_check", "prompt": "Go?"}]).met is True

    def test_async_gateway_inside_loop_errors_cleanly(self):
        class AsyncGateway:
            class llm:
                @staticmethod
                async def completion(prompt):
                    return "YES"

        ev = ConditionEvaluator(state_provider=lambda: {}, gateway=AsyncGateway())

        async def scenario():
            return ev.evaluate([{"type": "llm_check", "prompt": "Go?"}])

        result = asyncio.run(scenario())
        assert result.met is False
        assert "event loop" in result.reason


class TestErrorsAndUnknownTypes:
    def test_unknown_condition_type(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([{"type": "quantum_flux", "level": 11}])
        assert result.met is False
        assert "unknown condition type" in result.reason

    def test_state_provider_failure_reported(self):
        def boom():
            raise OSError("disk exploded")

        ev = ConditionEvaluator(state_provider=boom)
        result = ev.evaluate([{"type": "expression", "expr": "x < 1"}])
        assert result.met is False
        assert "state provider error" in result.reason

    def test_evaluation_error_stops_chain(self):
        calls = []
        ev = ConditionEvaluator(state_provider=lambda: {"ok": 1, "bad": None})

        # First condition errors (unknown var), so nothing else should matter.
        result = ev.evaluate(
            [
                {"type": "expression", "expr": "missing_var < 1"},
                {"type": "expression", "expr": "ok < 100"},
            ]
        )
        calls.append(result)
        assert result.met is False
        assert "missing_var" in result.reason

    def test_none_condition_entry_is_error(self):
        ev = ConditionEvaluator(state_provider=lambda: {})
        result = ev.evaluate([None])
        assert result.met is False
