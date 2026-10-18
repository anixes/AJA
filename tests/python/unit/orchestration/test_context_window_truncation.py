"""
test_context_window_truncation.py — Unit tests for P1.3 Synthetic Truncation Marker & Model Limits
================================================================================================
"""

import unittest
from typing import List, Dict, Any

from aja.orchestration.context_window import (
    TRUNCATION_NOTICE_PREFIX,
    compress_history,
    resolve_model_limit,
    atomic_prune_messages,
    _BUDGET_FRACTION,
)


class TestExpandedModelLimits(unittest.TestCase):
    def test_qwen_limit(self):
        limit = resolve_model_limit(model="qwen-2.5-coder-32b")
        self.assertEqual(limit, int(128_000 * _BUDGET_FRACTION))

    def test_glm_limit(self):
        limit = resolve_model_limit(model="glm-4-flash")
        self.assertEqual(limit, int(128_000 * _BUDGET_FRACTION))

    def test_deepseek_limit(self):
        limit = resolve_model_limit(model="deepseek-coder-v2")
        self.assertEqual(limit, int(64_000 * _BUDGET_FRACTION))

    def test_o3_limit(self):
        limit = resolve_model_limit(model="o3-mini")
        self.assertEqual(limit, int(200_000 * _BUDGET_FRACTION))


class TestSyntheticTruncationMarker(unittest.TestCase):
    def test_under_budget_leaves_history_untouched(self):
        history = [
            {"role": "user", "content": "Initial task objective"},
            {"role": "assistant", "content": "Acknowledged. Proceeding."},
            {"role": "user", "content": "Update status."},
            {"role": "assistant", "content": "Done."},
        ]
        compress_history(history, model="qwen-2.5-coder")
        self.assertEqual(len(history), 4)
        for msg in history:
            self.assertFalse(str(msg.get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX))

    def test_truncation_notice_inserted_at_index_1(self):
        history: List[Dict[str, Any]] = [
            {"role": "user", "content": "TASK_ROOT_OBJECTIVE"},
        ]
        # Append 50 large messages to exceed budget for a small model limit
        for i in range(50):
            history.append({"role": "user", "content": f"Turn {i}: " + ("x" * 3000)})
            history.append({"role": "assistant", "content": f"Reply {i}: " + ("y" * 3000)})

        initial_len = len(history)
        compress_history(history, model="copilot")  # Copilot limit is 12,288

        # First message must be preserved
        self.assertEqual(history[0]["content"], "TASK_ROOT_OBJECTIVE")
        # Total messages reduced
        self.assertLess(len(history), initial_len)
        # Synthetic notice must be inserted at index 1
        self.assertTrue(str(history[1].get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX))
        self.assertIn("prior interaction turn(s) were trimmed", history[1]["content"])
        self.assertIn("PLAN.md", history[1]["content"])

    def test_notice_accumulation_without_duplicates(self):
        history: List[Dict[str, Any]] = [
            {"role": "user", "content": "TASK_ROOT_OBJECTIVE"},
        ]
        for i in range(30):
            history.append({"role": "user", "content": f"Turn {i}: " + ("x" * 3000)})

        # First compression run
        compress_history(history, model="copilot")
        self.assertTrue(str(history[1].get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX))
        first_notice_content = history[1]["content"]

        # Count occurrences of TRUNCATION_NOTICE_PREFIX in history
        notices = [m for m in history if str(m.get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX)]
        self.assertEqual(len(notices), 1)

        # Append more oversized turns
        for i in range(20):
            history.append({"role": "user", "content": f"Later turn {i}: " + ("z" * 3000)})

        # Second compression run
        compress_history(history, model="copilot")

        # Root objective must still be at index 0
        self.assertEqual(history[0]["content"], "TASK_ROOT_OBJECTIVE")
        # Still exactly ONE notice at index 1 (no stacking or duplication)
        notices_after = [m for m in history if str(m.get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX)]
        self.assertEqual(len(notices_after), 1)
        self.assertTrue(str(history[1].get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX))

    def test_atomic_tool_calls_pruning_with_notice(self):
        history: List[Dict[str, Any]] = [
            {"role": "user", "content": "TASK_ROOT_OBJECTIVE"},
            {
                "role": "assistant",
                "content": "Running tool...",
                "tool_calls": [{"id": "call_1", "function": {"name": "run_bash"}}],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "OUTPUT " + ("x" * 30000)},
            {"role": "assistant", "content": "Analyzing result..."},
            {"role": "user", "content": "Final user query."},
        ]

        compress_history(history, model="copilot")

        # Objective must be preserved at index 0
        self.assertEqual(history[0]["content"], "TASK_ROOT_OBJECTIVE")
        # Notice must be at index 1
        self.assertTrue(str(history[1].get("content", "")).startswith(TRUNCATION_NOTICE_PREFIX))
        # Ensure no orphaned role='tool' message remains at index 1 or elsewhere without its assistant caller
        for idx, msg in enumerate(history):
            if msg.get("role") == "tool":
                self.assertGreater(idx, 1)
                self.assertEqual(history[idx - 1].get("role"), "assistant")
                self.assertIsNotNone(history[idx - 1].get("tool_calls"))

    def test_notice_metadata_flags(self):
        history: List[Dict[str, Any]] = [
            {"role": "user", "content": "TASK_ROOT_OBJECTIVE"},
        ]
        for i in range(20):
            history.append({"role": "user", "content": f"Turn {i}: " + ("x" * 3000)})

        compress_history(history, model="copilot")
        notice = history[1]
        self.assertTrue(notice.get("_synthetic"))
        self.assertIsInstance(notice.get("_trimmed_count"), int)
        self.assertGreater(notice["_trimmed_count"], 0)
    def test_context_window_compress_history_safe_halt(self):
        # Test that compress_history prunes when over limit and halts gracefully
        history = [
            {"role": "user", "content": "Initial prompt " + "x" * 200},
            {"role": "assistant", "content": "Response 1 " + "y" * 200},
            {"role": "user", "content": "Follow-up question " + "z" * 200},
            {"role": "assistant", "content": "Response 2 " + "w" * 200},
        ]

        compress_history(history, limit=50, history_floor=10)
        # History must have been pruned safely without infinite looping
        self.assertLessEqual(len(history), 4)


if __name__ == "__main__":
    unittest.main()
