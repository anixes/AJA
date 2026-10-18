"""
test_context_providers.py - Unit tests for Zed-style dynamic context providers.
==============================================================================
"""

from pathlib import Path
from aja.orchestration.context_providers import (
    resolve_file_context,
    resolve_symbol_context,
    resolve_git_diff_context,
    resolve_diagnostics_context,
    expand_context_tokens,
)


def test_resolve_file_context_existing(tmp_path: Path):
    test_file = tmp_path / "sample.py"
    test_file.write_text("x = 10\ny = 20\n", encoding="utf-8")

    ctx = resolve_file_context(test_file, workspace_root=tmp_path)
    assert "=== File Context: sample.py" in ctx
    assert "1: x = 10" in ctx
    assert "2: y = 20" in ctx


def test_resolve_file_context_missing(tmp_path: Path):
    ctx = resolve_file_context("non_existent.py", workspace_root=tmp_path)
    assert "File not found" in ctx


def test_resolve_symbol_context(tmp_path: Path):
    code = """
class Calculator:
    def add(self, a, b):
        return a + b

def multiply(a, b):
    return a * b
"""
    calc_file = tmp_path / "calc.py"
    calc_file.write_text(code, encoding="utf-8")

    ctx = resolve_symbol_context("Calculator", workspace_root=tmp_path)
    assert "=== Symbol 'Calculator'" in ctx
    assert "class Calculator:" in ctx

    func_ctx = resolve_symbol_context("multiply", workspace_root=tmp_path)
    assert "=== Symbol 'multiply'" in func_ctx
    assert "def multiply(a, b):" in func_ctx


def test_resolve_symbol_context_missing(tmp_path: Path):
    ctx = resolve_symbol_context("NoSuchSymbol", workspace_root=tmp_path)
    assert "Symbol 'NoSuchSymbol' not found" in ctx


def test_resolve_git_diff_context():
    ctx = resolve_git_diff_context()
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_resolve_diagnostics_context():
    ctx = resolve_diagnostics_context()
    assert "=== Diagnostics" in ctx


def test_expand_context_tokens(tmp_path: Path):
    sample = tmp_path / "dummy.txt"
    sample.write_text("hello world\n", encoding="utf-8")

    prompt = f"Please inspect @file:{sample.name} and let me know."
    expanded = expand_context_tokens(prompt, workspace_root=tmp_path)

    assert "[Grounded Context]" in expanded
    assert "File Context: dummy.txt" in expanded
    assert "hello world" in expanded
