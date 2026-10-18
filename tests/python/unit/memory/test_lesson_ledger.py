"""
test_lesson_ledger.py - Unit tests for LessonLedger episodic failure memory.
=============================================================================
"""

from pathlib import Path
import pytest

from aja.memory.lesson_ledger import Lesson, LessonLedger


def test_lesson_record_and_deduplication(tmp_path: Path):
    store_file = tmp_path / "lessons.json"
    ledger = LessonLedger(store_file)

    # 1. Record initial failure
    lesson1 = ledger.record_failure(
        task_objective="Implement database migration for users table",
        error_message="OperationalError: table users already exists line 42",
        error_type="DatabaseError",
        domain_tags=["database", "sql"],
        lesson_learned="Table was already created in prior run",
        mitigation_rule="Use CREATE TABLE IF NOT EXISTS in all migrations.",
    )

    assert lesson1.hit_count == 1
    assert "database" in lesson1.domain_tags
    assert ledger.count() == 1

    # 2. Record duplicate failure (varying only line number)
    lesson2 = ledger.record_failure(
        task_objective="Implement database migration for users table",
        error_message="OperationalError: table users already exists line 99",
        error_type="DatabaseError",
        domain_tags=["database"],
    )

    # Must deduplicate to same signature and increment hit_count
    assert lesson2.failure_signature == lesson1.failure_signature
    assert lesson2.hit_count == 2
    assert ledger.count() == 1


def test_lesson_query_by_tags_and_relevance(tmp_path: Path):
    store_file = tmp_path / "lessons.json"
    ledger = LessonLedger(store_file)

    ledger.record_failure(
        task_objective="Run pytest test suite",
        error_message="TimeoutError: test execution took > 60s",
        error_type="TimeoutError",
        domain_tags=["pytest", "tests"],
        mitigation_rule="Use -m 'not slow' for rapid verification cycles.",
    )

    ledger.record_failure(
        task_objective="Configure git remote",
        error_message="fatal: remote origin already exists",
        error_type="GitError",
        domain_tags=["git"],
        mitigation_rule="Check existing remotes with git remote -v before adding.",
    )

    # Query with tag filter
    results_git = ledger.query_lessons(tags=["git"])
    assert len(results_git) == 1
    assert results_git[0].domain_tags == ["git"]
    assert "remote" in results_git[0].mitigation_rule

    # Query by objective keyword
    results_test = ledger.query_lessons(objective="Run fast pytest checks")
    assert len(results_test) >= 1
    assert results_test[0].domain_tags == ["pytest", "tests"]


def test_lesson_prompt_formatting(tmp_path: Path):
    ledger = LessonLedger()
    l1 = ledger.record_failure(
        task_objective="Docker container build",
        error_message="Dockerfile: syntax error",
        error_type="BuildError",
        domain_tags=["docker"],
        lesson_learned="Missing base image tag",
        mitigation_rule="Always pin explicit base image versions.",
    )

    prompt = ledger.format_lessons_for_prompt([l1])
    assert "Lessons Learned" in prompt
    assert "Always pin explicit base image versions." in prompt
    assert "[docker]" in prompt


def test_lesson_persistence_roundtrip(tmp_path: Path):
    store_file = tmp_path / "persisted_lessons.json"
    ledger1 = LessonLedger(store_file)

    ledger1.record_failure(
        task_objective="Async websocket handshake",
        error_message="ConnectionResetError: server closed connection",
        error_type="NetworkError",
        domain_tags=["network", "websocket"],
        mitigation_rule="Add exponential backoff retry to websocket connect.",
    )
    assert ledger1.count() == 1

    # Instantiate separate ledger pointing to the same file
    ledger2 = LessonLedger(store_file)
    assert ledger2.count() == 1
    lessons = ledger2.query_lessons(tags=["websocket"])
    assert len(lessons) == 1
    assert lessons[0].mitigation_rule == "Add exponential backoff retry to websocket connect."
