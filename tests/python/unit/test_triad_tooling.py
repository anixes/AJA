"""
tests/python/unit/test_triad_tooling.py - Unit test suite for scripts/triad_review.py.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")

from scripts.triad_review import (
    DEFAULT_SYSTEM_CONSULT_PROMPT,
    DEFAULT_SYSTEM_REVIEW_PROMPT,
    ModelReviewResult,
    build_review_prompt,
    extract_response_text,
    extract_score_and_verdict,
    generate_consolidated_report,
    get_git_diff,
    load_test_files,
    query_model_with_retry,
)


def test_extract_score_and_verdict_approved():
    sample = """
    ## Code Review
    The implementation is clean, modular, and resilient.
    Score: 9.0/10
    Verdict: APPROVED
    """
    score, verdict = extract_score_and_verdict(sample)
    assert score == 9.0
    assert verdict == "APPROVED"


def test_extract_score_and_verdict_approved_with_comments():
    sample = """
    Production Readiness Score: 8.5 / 10
    Status: APPROVED WITH COMMENTS
    Please address the subprocess PGID isolation before merge.
    """
    score, verdict = extract_score_and_verdict(sample)
    assert score == 8.5
    assert verdict == "APPROVED WITH COMMENTS"


def test_extract_score_and_verdict_rejected():
    sample = """
    Critical security flaw: path traversal allows escaping workspace sandbox.
    Score: 4.5/10
    Verdict: REJECTED
    """
    score, verdict = extract_score_and_verdict(sample)
    assert score == 4.5
    assert verdict == "REJECTED"


def test_extract_response_text_standard():
    choice = MagicMock()
    choice.message.content = "Standard review content here."
    choice.message.reasoning = None
    choice.message.reasoning_content = None

    content, reasoning, final_text = extract_response_text(choice)
    assert content == "Standard review content here."
    assert reasoning == ""
    assert final_text == "Standard review content here."


def test_extract_response_text_reasoning_fallback():
    choice = MagicMock()
    choice.message.content = ""
    choice.message.reasoning = "Deep architectural thoughts and review findings."
    choice.message.reasoning_content = None

    content, reasoning, final_text = extract_response_text(choice)
    assert content == ""
    assert reasoning == "Deep architectural thoughts and review findings."
    assert final_text == "Deep architectural thoughts and review findings."


def test_load_test_files_budget_truncation(tmp_path: Path):
    t1 = tmp_path / "test_a.py"
    t1.write_text("def test_one(): pass\n" * 10, encoding="utf-8")
    t2 = tmp_path / "test_b.py"
    t2.write_text("def test_two(): pass\n" * 10, encoding="utf-8")

    loaded = load_test_files([str(t1), str(t2)], max_total_chars=100)
    assert "test_a.py" in loaded
    assert "Truncated" in loaded or "test_b.py" in loaded


def test_build_review_prompt():
    prompt = build_review_prompt(
        diff_text="+ new code line",
        test_code="def test_sample(): assert True",
        custom_instructions="Focus on concurrency.",
    )
    assert "+ new code line" in prompt
    assert "def test_sample(): assert True" in prompt
    assert "Focus on concurrency." in prompt
    assert "Score: X/10" in prompt


def test_generate_consolidated_report(tmp_path: Path):
    out_file1 = tmp_path / "model1_review.md"
    out_file1.write_text("Review details for Model 1\nScore: 9.0/10\nVerdict: APPROVED", encoding="utf-8")

    out_file2 = tmp_path / "model2_review.md"
    out_file2.write_text("Review details for Model 2\nScore: 8.5/10\nVerdict: APPROVED WITH COMMENTS", encoding="utf-8")

    results = [
        ModelReviewResult(
            model="vendor/model-alpha",
            label="Reviewer 1",
            success=True,
            score=9.0,
            verdict="APPROVED",
            duration_s=4.2,
            output_file=out_file1,
        ),
        ModelReviewResult(
            model="vendor/model-beta",
            label="Reviewer 2",
            success=True,
            score=8.5,
            verdict="APPROVED WITH COMMENTS",
            duration_s=5.1,
            output_file=out_file2,
        ),
    ]

    report_path = tmp_path / "consolidated_report.md"
    report_text = generate_consolidated_report("review", results, report_path)

    assert "Triad Evaluation Report: REVIEW" in report_text
    assert "vendor/model-alpha" in report_text
    assert "vendor/model-beta" in report_text
    assert "9.0/10" in report_text
    assert "8.5/10" in report_text
    assert report_path.exists()


def test_query_model_with_retry_success():
    async def _run():
        client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Review passed cleanly."
        mock_choice.message.reasoning = None
        mock_choice.message.reasoning_content = None

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        client.chat.completions.create = AsyncMock(return_value=mock_resp)

        success, content, reasoning, final_text, err = await query_model_with_retry(
            client=client,
            model="test-model",
            system_prompt="system",
            user_prompt="user",
            max_retries=2,
        )
        assert success is True
        assert final_text == "Review passed cleanly."
        assert err is None

    import asyncio
    asyncio.run(_run())


def test_query_model_with_retry_backoff_on_429():
    async def _run():
        client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Eventually succeeded after 429 rate limit."
        mock_choice.message.reasoning = None
        mock_choice.message.reasoning_content = None

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        client.chat.completions.create = AsyncMock(
            side_effect=[
                Exception("HTTP 429: Too Many Requests (Rate limit reached)"),
                mock_resp,
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            success, content, reasoning, final_text, err = await query_model_with_retry(
                client=client,
                model="test-model",
                system_prompt="system",
                user_prompt="user",
                max_retries=2,
                initial_backoff=0.01,
            )
            assert success is True
            assert final_text == "Eventually succeeded after 429 rate limit."
            assert mock_sleep.called

    import asyncio
    asyncio.run(_run())
