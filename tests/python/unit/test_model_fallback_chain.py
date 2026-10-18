"""
test_model_fallback_chain.py — Comprehensive unit tests for Phase 2 Dynamic Model Chain & Fallbacks
==================================================================================================
Strictly model-agnostic: uses arbitrary dummy identifiers (e.g. tier1-fast, tier2-mid, tier3-heavy).
"""

import unittest
from unittest.mock import MagicMock
from typing import List, Dict, Any, Optional

from aja.config_schema import (
    SwarmSettings,
    parse_model_chain,
    resolve_model_chain,
)
from aja.orchestration.errors import (
    classify_gateway_error,
    GatewayError,
    RateLimitError,
    ProviderUnavailableError,
    ModelNotFoundError,
    ContextOverflowError,
    FatalRequestError,
    ChainExhaustedError,
)
from aja.orchestration.direct_loop import (
    run_direct_loop,
    DirectLoopHooks,
)


class TestModelChainParsingAndResolution(unittest.TestCase):
    def test_parse_model_chain_list(self):
        result = parse_model_chain(["provider-a:model-1", "provider-b:model-2"])
        self.assertEqual(result, ["provider-a:model-1", "provider-b:model-2"])

    def test_parse_model_chain_json_string(self):
        json_str = '["alpha:m1", "beta:m2", "alpha:m1"]'  # Contains duplicate
        result = parse_model_chain(json_str)
        # Should deduplicate preserving order
        self.assertEqual(result, ["alpha:m1", "beta:m2"])

    def test_parse_model_chain_comma_separated(self):
        raw = " model-x , model-y , model-x , model-z "
        result = parse_model_chain(raw)
        self.assertEqual(result, ["model-x", "model-y", "model-z"])

    def test_parse_model_chain_arrow_separated(self):
        raw = "tier1-fast > tier2-heavy > tier3-backup"
        result = parse_model_chain(raw)
        self.assertEqual(result, ["tier1-fast", "tier2-heavy", "tier3-backup"])

    def test_resolve_priority_caller_overrides_env_and_config(self):
        settings = SwarmSettings(fallback_chain=["config-model"])
        env = {"AJA_MODEL_CHAIN": '["env-model"]'}
        caller = ["caller-model-1", "caller-model-2"]

        resolved = resolve_model_chain(caller_chain=caller, settings=settings, env=env)
        self.assertEqual(resolved, ["caller-model-1", "caller-model-2"])

    def test_resolve_priority_env_overrides_config(self):
        settings = SwarmSettings(fallback_chain=["config-model"])
        env = {"AJA_FALLBACK_MODELS": "env-model-a, env-model-b"}

        resolved = resolve_model_chain(caller_chain=None, settings=settings, env=env)
        self.assertEqual(resolved, ["env-model-a", "env-model-b"])

    def test_resolve_priority_config_used_when_no_env_or_caller(self):
        settings = SwarmSettings(fallback_chain=["config-model-1", "config-model-2"])
        env = {}

        resolved = resolve_model_chain(caller_chain=None, settings=settings, env=env)
        self.assertEqual(resolved, ["config-model-1", "config-model-2"])

    def test_resolve_single_model_fallback(self):
        settings = SwarmSettings()
        env = {}
        resolved = resolve_model_chain(model="my-custom-model", settings=settings, env=env)
        self.assertEqual(resolved, ["my-custom-model"])


class TestErrorClassification(unittest.TestCase):
    def test_classify_rate_limit_429(self):
        class HttpError(Exception):
            status_code = 429
        
        err = classify_gateway_error(HttpError("Too Many Requests"), model="test-m")
        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(err.is_fallback_candidate)
        self.assertEqual(err.model, "test-m")

    def test_classify_quota_exhausted_message(self):
        err = classify_gateway_error(Exception("insufficient_quota: you exceeded your current quota"), model="test-m")
        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(err.is_fallback_candidate)

    def test_classify_provider_unavailable_503(self):
        class HttpError(Exception):
            status = 503
        
        err = classify_gateway_error(HttpError("Service Unavailable"), model="test-m")
        self.assertIsInstance(err, ProviderUnavailableError)
        self.assertTrue(err.is_fallback_candidate)

    def test_classify_model_not_found_404(self):
        class HttpError(Exception):
            status_code = 404
        
        err = classify_gateway_error(HttpError("Model not found"), model="test-m")
        self.assertIsInstance(err, ModelNotFoundError)
        self.assertTrue(err.is_fallback_candidate)

    def test_classify_context_overflow_message(self):
        err = classify_gateway_error(Exception("maximum context length is 8192 tokens. however, your messages resulted in 10000"), model="test-m")
        self.assertIsInstance(err, ContextOverflowError)
        self.assertTrue(err.is_fallback_candidate)

    def test_classify_fatal_syntax_error(self):
        class BadRequestError(Exception):
            status_code = 400
        err = classify_gateway_error(BadRequestError("Invalid JSON in parameter"), model="test-m")
        self.assertIsInstance(err, FatalRequestError)
        self.assertFalse(err.is_fallback_candidate)
        self.assertTrue(err.is_fatal)


class MockResponse:
    def __init__(self, content: str = "", tool_calls: Optional[List[Dict[str, Any]]] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockFallbackGateway:
    def __init__(self, failure_models: Optional[Dict[str, Exception]] = None, success_response: Any = "Task done."):
        self.failure_models = failure_models or {}
        self.success_response = success_response
        self.models_called: List[str] = []

    async def chat(self, model=None, prompt=None, system=None, tools=None):
        self.models_called.append(model)
        if model in self.failure_models:
            raise self.failure_models[model]
        return self.success_response


class MockRegistry:
    def get_schemas(self, interactive=True, profile=None):
        return []


class MockExecutor:
    async def dispatch_tool_calls(self, tool_calls, trace_id, dry_run=False):
        return []


class TestDirectLoopModelFallback(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_advances_to_next_model_on_rate_limit(self):
        fallback_events = []
        hooks = DirectLoopHooks(
            on_model_fallback=lambda f, t, e, a: fallback_events.append((f, t, type(e), a))
        )

        class Http429(Exception):
            status_code = 429

        gateway = MockFallbackGateway(
            failure_models={"tier1-fast": Http429("Rate limit exceeded")},
            success_response="Completed via tier2.",
        )

        models_compressed = []

        def test_compressor(history, model=None, **kwargs):
            models_compressed.append(model)

        chain = ["tier1-fast", "tier2-medium"]
        result = await run_direct_loop(
            objective="Do task",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            fallback_chain=chain,
            hooks=hooks,
            history_compressor=test_compressor,
        )

        # Gateway called tier1-fast (failed) then tier2-medium (succeeded)
        self.assertEqual(gateway.models_called, ["tier1-fast", "tier2-medium"])
        # on_model_fallback hook fired once
        self.assertEqual(len(fallback_events), 1)
        from_m, to_m, err_type, attempt = fallback_events[0]
        self.assertEqual(from_m, "tier1-fast")
        self.assertEqual(to_m, "tier2-medium")
        self.assertEqual(err_type, RateLimitError)
        self.assertEqual(attempt, 1)

        # Context compression was called for both models
        self.assertIn("tier1-fast", models_compressed)
        self.assertIn("tier2-medium", models_compressed)

    async def test_chain_exhaustion_raises_error(self):
        exhausted_events = []
        hooks = DirectLoopHooks(
            on_chain_exhausted=lambda ch, errs: exhausted_events.append((ch, len(errs)))
        )

        class Http503(Exception):
            status_code = 503

        gateway = MockFallbackGateway(
            failure_models={
                "model-a": Http503("Service unavailable"),
                "model-b": Http503("Service unavailable"),
            }
        )

        chain = ["model-a", "model-b"]
        with self.assertRaises(ChainExhaustedError):
            await run_direct_loop(
                objective="Do task",
                gateway=gateway,
                tools_registry=MockRegistry(),
                executor=MockExecutor(),
                fallback_chain=chain,
                hooks=hooks,
            )

        # Both models were tried
        self.assertEqual(gateway.models_called, ["model-a", "model-b"])
        # on_chain_exhausted hook fired
        self.assertEqual(len(exhausted_events), 1)
        ch, err_count = exhausted_events[0]
        self.assertEqual(ch, ["model-a", "model-b"])
        self.assertEqual(err_count, 2)

    async def test_fatal_error_does_not_fallback(self):
        fallback_events = []
        hooks = DirectLoopHooks(
            on_model_fallback=lambda f, t, e, a: fallback_events.append((f, t, e))
        )

        class Http400(Exception):
            status_code = 400

        gateway = MockFallbackGateway(
            failure_models={"model-1": Http400("Fatal syntax validation error")}
        )

        chain = ["model-1", "model-2"]
        with self.assertRaises(Http400):
            await run_direct_loop(
                objective="Do task",
                gateway=gateway,
                tools_registry=MockRegistry(),
                executor=MockExecutor(),
                fallback_chain=chain,
                hooks=hooks,
            )

        # Only model-1 was called; fatal error did NOT fallback to model-2
        self.assertEqual(gateway.models_called, ["model-1"])
        self.assertEqual(len(fallback_events), 0)

    async def test_fallback_resets_per_turn_to_primary_model(self):
        """Verify cost-aware behavior: Turn 1 falls back to model-2, but Turn 2 retries model-1 first."""
        calls_per_turn = []

        class MultiTurnGateway:
            def __init__(self):
                self.turn_count = 0
                self.history_models = []

            async def chat(self, model, prompt, system, tools=None):
                self.history_models.append(model)
                if len(self.history_models) == 1:
                    # Turn 1, attempt 1: primary model fails
                    class Http429(Exception):
                        status_code = 429
                    raise Http429("Rate limit on primary model")
                elif len(self.history_models) == 2:
                    # Turn 1, attempt 2: secondary model succeeds with tool call
                    return {
                        "content": "Action needed",
                        "tool_calls": [
                            {"id": "call_1", "function": {"name": "read_file", "arguments": '{"path": "foo.txt"}'}}
                        ],
                    }
                elif len(self.history_models) == 3:
                    # Turn 2, attempt 1: MUST be model-1 again!
                    return {"content": "Task completed successfully.", "tool_calls": []}
                return {"content": "Done", "tool_calls": []}

        gateway = MultiTurnGateway()
        chain = ["primary-cheap", "secondary-expensive"]

        res = await run_direct_loop(
            objective="Multi-turn task",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            fallback_chain=chain,
            max_turns=3,
        )

        self.assertEqual(res["status"], "completed")
        # Turn 1 called primary (failed), then secondary (succeeded).
        # Turn 2 called primary FIRST and succeeded!
        self.assertEqual(gateway.history_models, ["primary-cheap", "secondary-expensive", "primary-cheap"])

    async def test_settings_fallback_chain_integration(self):
        """Verify SwarmSettings.fallback_chain is consulted by run_direct_loop when no caller chain is passed."""
        gateway = MockFallbackGateway(
            failure_models={"cfg-tier1": Exception("503 Service Unavailable")}
        )
        settings = SwarmSettings(fallback_chain=["cfg-tier1", "cfg-tier2"])

        res = await run_direct_loop(
            objective="Do task with settings",
            gateway=gateway,
            tools_registry=MockRegistry(),
            executor=MockExecutor(),
            settings=settings,
        )

        self.assertEqual(res["status"], "completed")
        self.assertEqual(gateway.models_called, ["cfg-tier1", "cfg-tier2"])

    async def test_empty_chain_raises_value_error(self):
        """Verify an explicit error is raised if no models can be resolved."""
        gateway = MockFallbackGateway()
        with self.assertRaises(ValueError) as ctx:
            await run_direct_loop(
                objective="Do task with empty chain",
                gateway=gateway,
                tools_registry=MockRegistry(),
                executor=MockExecutor(),
                model="",
                fallback_chain=[],
                settings=SwarmSettings(fallback_chain=[]),
            )
        self.assertIn("No active model or fallback chain", str(ctx.exception))


class TestErrorClassificationRefinements(unittest.TestCase):
    def test_word_boundary_rate_limit(self):
        # "4290 tokens" must NOT match rate limit pattern
        err_non_rate = classify_gateway_error(Exception("Processed 4290 tokens in prompt"), model="test-m")
        self.assertIsInstance(err_non_rate, FatalRequestError)

        # "429 Too Many Requests" MUST match
        err_rate = classify_gateway_error(Exception("Error: 429 Too Many Requests"), model="test-m")
        self.assertIsInstance(err_rate, RateLimitError)

    def test_timeout_and_connection_error_classified_as_unavailable(self):
        # Native TimeoutError
        err_timeout = classify_gateway_error(TimeoutError("Connection timed out"), model="test-m")
        self.assertIsInstance(err_timeout, ProviderUnavailableError)
        self.assertTrue(err_timeout.is_fallback_candidate)

        # String network error
        err_net = classify_gateway_error(Exception("ClientConnectorError: connection refused"), model="test-m")
        self.assertIsInstance(err_net, ProviderUnavailableError)
        self.assertTrue(err_net.is_fallback_candidate)


if __name__ == "__main__":
    unittest.main()
