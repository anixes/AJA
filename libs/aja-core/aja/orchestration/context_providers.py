"""
context_providers.py - Dynamic Context Providers for AJA (Zed IDE Pattern).
===========================================================================
Enables precision context grounding via `@` tokens in prompts:
- `@file:<path>`: Injects file content with line numbers.
- `@symbol:<name>`: Extracts exact class/function definition via AST without loading full files.
- `@diff`: Injects current uncommitted git changes.
- `@diagnostics`: Injects workspace syntax/linter diagnostics.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


def resolve_file_context(file_path: str | Path, workspace_root: Optional[Path] = None) -> str:
    """Load file contents formatted with line numbers."""
    root = workspace_root or Path.cwd()
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path

    if not path.exists():
        return f"[Context Error: File not found: '{file_path}']"
    if not path.is_file():
        return f"[Context Error: Path is not a file: '{file_path}']"

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        max_lines = 300
        truncated = len(lines) > max_lines
        selected_lines = lines[:max_lines]

        formatted = [f"{i+1:4d}: {line}" for i, line in enumerate(selected_lines)]
        output = [f"=== File Context: {path.name} ({len(lines)} lines) ==="]
        output.extend(formatted)
        if truncated:
            output.append(f"... [Truncated {len(lines) - max_lines} lines] ...")
        output.append("=== End File Context ===")
        return "\n".join(output)
    except Exception as e:
        return f"[Context Error reading '{file_path}': {e}]"


def resolve_symbol_context(symbol_name: str, workspace_root: Optional[Path] = None) -> str:
    """Find and extract a class or function definition via AST across Python files."""
    root = workspace_root or Path.cwd()
    matches: List[Tuple[Path, int, int, str]] = []

    # Search python files (skip venv, .git, etc.)
    py_files = [
        p for p in root.glob("**/*.py")
        if "venv" not in p.parts and ".git" not in p.parts and ".system_generated" not in p.parts
    ][:100]

    for py_path in py_files:
        try:
            source = py_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_path))
            lines = source.splitlines()

            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == symbol_name:
                        start_line = node.lineno
                        end_line = getattr(node, "end_lineno", start_line + 20)
                        extracted = lines[start_line - 1 : end_line]
                        formatted = [f"{start_line + i:4d}: {line}" for i, line in enumerate(extracted)]
                        matches.append((py_path, start_line, end_line, "\n".join(formatted)))
        except Exception:
            continue

    if not matches:
        return f"[Context Warning: Symbol '{symbol_name}' not found in workspace]"

    blocks = []
    for path, s_line, e_line, code in matches[:3]:
        rel_path = path.relative_to(root) if path.is_relative_to(root) else path.name
        blocks.append(
            f"=== Symbol '{symbol_name}' in {rel_path}:{s_line}-{e_line} ===\n{code}\n=== End Symbol ==="
        )

    return "\n\n".join(blocks)


def resolve_git_diff_context(workspace_root: Optional[Path] = None) -> str:
    """Fetch uncommitted git diff in the workspace."""
    root = str(workspace_root or Path.cwd())
    try:
        res = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        diff = res.stdout.strip()
        if not diff:
            # Check for unstaged/staged untracked changes
            res_stat = subprocess.run(
                ["git", "status", "--short"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            status = res_stat.stdout.strip()
            if status:
                return f"=== Git Status (No unstaged line diffs) ===\n{status}\n=== End Git Status ==="
            return "[Context: Git working tree clean, no changes]"

        max_chars = 3000
        if len(diff) > max_chars:
            diff = diff[:max_chars] + "\n... [Git diff truncated] ..."

        return f"=== Uncommitted Git Diff ===\n{diff}\n=== End Git Diff ==="
    except Exception as e:
        return f"[Context Error fetching git diff: {e}]"


def resolve_diagnostics_context(workspace_root: Optional[Path] = None) -> str:
    """Perform quick syntax validation diagnostics across project files."""
    root = workspace_root or Path.cwd()
    from aja.orchestration.verification_runner import verify_python_syntax

    py_files = [
        p for p in root.glob("**/*.py")
        if "venv" not in p.parts and ".git" not in p.parts and ".system_generated" not in p.parts
    ][:50]

    res = verify_python_syntax(py_files)
    if res.passed:
        return f"=== Diagnostics ===\n{res.summary}\n=== End Diagnostics ==="
    return f"=== Diagnostics (Failures Detected) ===\n{res.details}\n=== End Diagnostics ==="


def expand_context_tokens(prompt: str, workspace_root: Optional[Path] = None) -> str:
    """
    Parse and ground `@` context tokens in the prompt.
    Tokens supported:
    - `@file:<path>`
    - `@symbol:<name>`
    - `@diff`
    - `@diagnostics`
    """
    root = workspace_root or Path.cwd()
    attached_contexts: List[str] = []

    # 1. Expand @file:<path>
    file_pattern = r"@file:([^\s]+)"
    for match in re.finditer(file_pattern, prompt):
        f_path = match.group(1)
        attached_contexts.append(resolve_file_context(f_path, root))

    # 2. Expand @symbol:<name>
    symbol_pattern = r"@symbol:([a-zA-Z_][a-zA-Z0-9_]*)"
    for match in re.finditer(symbol_pattern, prompt):
        sym = match.group(1)
        attached_contexts.append(resolve_symbol_context(sym, root))

    # 3. Expand @diff
    if "@diff" in prompt:
        attached_contexts.append(resolve_git_diff_context(root))

    # 4. Expand @diagnostics
    if "@diagnostics" in prompt:
        attached_contexts.append(resolve_diagnostics_context(root))

    if not attached_contexts:
        return prompt

    context_block = "\n\n".join(attached_contexts)
    return f"{prompt}\n\n[Grounded Context]\n{context_block}"
