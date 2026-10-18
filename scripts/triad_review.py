#!/usr/bin/env python3
"""
scripts/triad_review.py - Permanent Triad Multi-Model Review & Consultation Tool.

Provides automated, multi-model architectural consultation and peer code review
across external AI models (e.g. via OpenAI-compatible gateways like Constellation Gate AI).

Features:
- Dual modes: 'review' (git diff + test analysis) and 'consult' (spec & architecture design).
- Automatic extraction of both standard 'content' and 'reasoning'/'reasoning_content' tokens.
- Token budgeting and intelligent diff compaction (-U2 context, line capping).
- Automatic retry with exponential backoff on HTTP 429 (rate limits) and 5xx errors.
- Structured Markdown reporting with scores, verdicts, and actionable findings saved to disk.
- CI/Pre-commit gate support with --strict (exits non-zero on REJECTED or score < threshold).
- Completely data-driven: model names and endpoints are loaded from environment or CLI flags,
  ensuring ZERO hardcoded model identifiers in the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

# Support UTF-8 encoding on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Optional dependency loading
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from openai import AsyncOpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


DEFAULT_SYSTEM_REVIEW_PROMPT = """You are a Principal Software Quality and Security Architect.
Conduct a strict, thorough peer review of the provided code diff and test suite:
1. Architectural soundness & fidelity to specifications.
2. Cross-platform safety (Windows and POSIX compatibility, path handling, process management).
3. Security posture (path confinement, injection risks, resource leakages).
4. Edge cases & error handling (timeouts, unexpected process terminations, state leaks).
5. Code style, maintainability, and clean separation of concerns.
6. Test suite completeness (coverage of edge cases, regression resilience).
7. Production Readiness Score (format as 'Score: X/10') and Verdict ('APPROVED', 'APPROVED WITH COMMENTS', or 'REJECTED').
Keep your review structured, highly technical, and direct with concrete code suggestions.
"""

DEFAULT_SYSTEM_CONSULT_PROMPT = """You are a Principal Software Systems Architect.
Provide rigorous, production-grade architectural guidance and design for the proposed system or task:
1. Architectural decomposition, modular interfaces, and data flow.
2. Edge case identification, failure modes, and recovery mechanisms.
3. Cross-platform considerations (Windows, macOS, Linux).
4. Concrete Python/framework implementation examples and patterns.
5. Performance, scalability, and resource efficiency.
Deliver your analysis cleanly structured with concrete technical directives.
"""


@dataclass
class ModelReviewResult:
    model: str
    label: str
    success: bool
    content: str = ""
    reasoning: str = ""
    score: Optional[float] = None
    verdict: str = "UNKNOWN"
    duration_s: float = 0.0
    error: Optional[str] = None
    output_file: Optional[Path] = None


def extract_score_and_verdict(text: str) -> Tuple[Optional[float], str]:
    """Parse numeric score and verdict from model response text."""
    score: Optional[float] = None
    verdict: str = "UNKNOWN"

    score_match = re.search(r"(?:score|rating):\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10", text, re.IGNORECASE)
    if not score_match:
        score_match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*/\s*10\b", text)
    if score_match:
        try:
            score = float(score_match.group(1))
        except ValueError:
            pass

    if re.search(r"\bAPPROVED\s+WITH\s+COMMENTS\b", text, re.IGNORECASE):
        verdict = "APPROVED WITH COMMENTS"
    elif re.search(r"\bREJECTED\b", text, re.IGNORECASE):
        verdict = "REJECTED"
    elif re.search(r"\bAPPROVED\b", text, re.IGNORECASE):
        verdict = "APPROVED"

    return score, verdict


def extract_response_text(choice: Any) -> Tuple[str, str, str]:
    """
    Uniformly extract standard content, reasoning tokens, and merged final text.
    Handles extended-thinking models where review output may be in reasoning/reasoning_content.
    """
    content = getattr(choice.message, "content", "") or ""
    reasoning = (
        getattr(choice.message, "reasoning", None)
        or getattr(choice.message, "reasoning_content", None)
        or ""
    )
    final_text = content.strip() if content.strip() else reasoning.strip()
    return content, reasoning, final_text


def get_git_diff(target: str = "unstaged", max_lines: int = 2500) -> str:
    """Extract git diff with compact 2-line context to preserve token budget."""
    cmd = ["git", "diff", "-U2"]
    if target == "staged":
        cmd.append("--cached")
    elif target != "unstaged" and target != "working":
        cmd.append(target)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        diff_text = res.stdout
    except Exception as e:
        return f"Error gathering git diff: {e}"

    lines = diff_text.splitlines()
    if len(lines) > max_lines:
        truncated_header = (
            f"# [Notice] Git diff truncated to first {max_lines} of {len(lines)} lines.\n"
        )
        return truncated_header + "\n".join(lines[:max_lines])
    return diff_text


def load_test_files(test_paths: List[str], max_total_chars: int = 35000) -> str:
    """Load specified test files up to a safe character budget."""
    output_blocks = []
    total_chars = 0

    for path_str in test_paths:
        p = Path(path_str)
        if not p.is_file():
            continue
        try:
            content = p.read_text(encoding="utf-8")
            if total_chars + len(content) > max_total_chars:
                budget_left = max(0, max_total_chars - total_chars)
                output_blocks.append(
                    f"### File: {p.as_posix()} (Truncated):\n```python\n{content[:budget_left]}\n...\n```"
                )
                break
            output_blocks.append(
                f"### File: {p.as_posix()}:\n```python\n{content}\n```"
            )
            total_chars += len(content)
        except Exception as e:
            output_blocks.append(f"### File: {p.as_posix()}: (Read error: {e})")

    return "\n\n".join(output_blocks)


async def query_model_with_retry(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 6000,
    temperature: float = 0.2,
    max_retries: int = 3,
    initial_backoff: float = 2.0,
) -> Tuple[bool, str, str, str, Optional[str]]:
    """Query a model with exponential backoff on HTTP 429 and transient 5xx errors."""
    backoff = initial_backoff
    last_err = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            choice = response.choices[0]
            content, reasoning, final_text = extract_response_text(choice)
            return True, content, reasoning, final_text, None
        except Exception as e:
            last_err = str(e)
            is_rate_limit = "429" in last_err or "rate limit" in last_err.lower()
            is_server_err = "500" in last_err or "502" in last_err or "503" in last_err
            if (is_rate_limit or is_server_err) and attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2.0
                continue
            break

    return False, "", "", "", last_err


async def run_triad_evaluation(
    mode: str,
    models: List[str],
    gateway_url: str,
    api_key: str,
    user_prompt: str,
    system_prompt: str,
    output_dir: Path,
    labels: Optional[List[str]] = None,
    max_tokens: int = 6000,
) -> List[ModelReviewResult]:
    """Execute evaluation across all configured models concurrently."""
    if not HAS_OPENAI:
        raise RuntimeError("openai package is required. Install via 'pip install openai'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    client = AsyncOpenAI(api_key=api_key, base_url=gateway_url)

    async def _worker(model_id: str, label_name: str) -> ModelReviewResult:
        t0 = time.perf_counter()
        clean_model_tag = re.sub(r"[^a-zA-Z0-9_\-]", "_", model_id).lower()
        out_file = output_dir / f"{clean_model_tag}_{mode}.md"

        success, content, reasoning, final_text, err = await query_model_with_retry(
            client=client,
            model=model_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
        )
        duration = time.perf_counter() - t0

        score, verdict = extract_score_and_verdict(final_text)

        if success and final_text:
            out_file.write_text(final_text, encoding="utf-8")
            return ModelReviewResult(
                model=model_id,
                label=label_name,
                success=True,
                content=content,
                reasoning=reasoning,
                score=score,
                verdict=verdict,
                duration_s=duration,
                output_file=out_file,
            )
        else:
            return ModelReviewResult(
                model=model_id,
                label=label_name,
                success=False,
                duration_s=duration,
                error=err or "Empty response",
                output_file=out_file,
            )

    tasks = []
    for idx, m in enumerate(models):
        lbl = labels[idx] if (labels and idx < len(labels)) else f"Model #{idx+1}"
        tasks.append(_worker(m, lbl))

    results = await asyncio.gather(*tasks)
    return list(results)


def build_review_prompt(diff_text: str, test_code: str, custom_instructions: str = "") -> str:
    """Assemble code review prompt payload."""
    blocks = [
        "Please conduct a comprehensive, technical peer review of the following changes:\n",
        "### 1. Git Changes (-U2 concise context):",
        "```diff",
        diff_text.strip() or "(No changes detected in target diff)",
        "```",
    ]
    if test_code.strip():
        blocks.extend([
            "\n### 2. Accompanying Unit & Integration Test Suites:",
            test_code.strip(),
        ])
    if custom_instructions.strip():
        blocks.extend([
            "\n### 3. Additional Specific Review Directives:",
            custom_instructions.strip(),
        ])
    blocks.append("\nPlease output your structured review with clear findings, a numeric score (Score: X/10), and final verdict.")
    return "\n".join(blocks)


def generate_consolidated_report(
    mode: str,
    results: List[ModelReviewResult],
    output_path: Path,
) -> str:
    """Format consolidated summary table and markdown report."""
    lines = [
        f"# Triad Evaluation Report: {mode.upper()}",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Summary Table",
        "| Model / Reviewer | Status | Score | Verdict | Latency | Report File |",
        "| :--- | :---: | :---: | :---: | :---: | :--- |",
    ]

    for r in results:
        status_icon = "✔ SUCCESS" if r.success else f"✘ FAILED ({r.error})"
        score_str = f"{r.score:.1f}/10" if r.score is not None else "N/A"
        file_link = f"[{r.output_file.name}]({r.output_file.as_posix()})" if r.output_file else "None"
        lines.append(
            f"| **{r.label}** (`{r.model}`) | {status_icon} | {score_str} | **{r.verdict}** | {r.duration_s:.1f}s | {file_link} |"
        )

    lines.append("\n---\n")
    lines.append("## Individual Review Summaries\n")

    for r in results:
        lines.append(f"### {r.label} (`{r.model}`)")
        if r.success and r.output_file and r.output_file.exists():
            content = r.output_file.read_text(encoding="utf-8")
            summary_snippet = content[:1500].strip()
            lines.append(f"```markdown\n{summary_snippet}\n...\n```\n")
        else:
            lines.append(f"**Error:** {r.error}\n")

    full_report = "\n".join(lines)
    output_path.write_text(full_report, encoding="utf-8")
    return full_report


def main():
    parser = argparse.ArgumentParser(
        description="AJA Triad Multi-Model Peer Review & Consultation Tool"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. review command
    review_parser = subparsers.add_parser("review", help="Run multi-model peer review on git changes")
    review_parser.add_argument(
        "--target",
        choices=["unstaged", "staged", "working", "HEAD~1"],
        default="unstaged",
        help="Git diff target to evaluate (default: unstaged)",
    )
    review_parser.add_argument(
        "--tests",
        nargs="*",
        default=[],
        help="Paths to test files to evaluate alongside diff",
    )
    review_parser.add_argument(
        "--instructions",
        type=str,
        default="",
        help="Additional specific instructions or focus areas for reviewers",
    )
    review_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero exit code if any model rejects or gives score < threshold",
    )
    review_parser.add_argument(
        "--min-score",
        type=float,
        default=7.0,
        help="Minimum passing score for strict mode (default: 7.0)",
    )

    # 2. consult command
    consult_parser = subparsers.add_parser("consult", help="Run multi-model architectural consultation")
    consult_parser.add_argument(
        "--spec",
        type=str,
        default=None,
        help="Path to markdown specification or design document to consult on",
    )
    consult_parser.add_argument(
        "--prompt",
        type=str,
        default="",
        help="Text prompt describing the architectural task or questions",
    )

    # Common options
    for p in (review_parser, consult_parser):
        p.add_argument(
            "--models",
            type=str,
            default=os.getenv("TRIAD_MODELS"),
            help="Comma-separated model identifiers (default: from TRIAD_MODELS env)",
        )
        p.add_argument(
            "--gateway-url",
            type=str,
            default=os.getenv("GATE_API_BASE", "https://gateway.constellationgate.ai/v1"),
            help="Base URL for OpenAI-compatible gateway (default: GATE_API_BASE env)",
        )
        p.add_argument(
            "--api-key",
            type=str,
            default=os.getenv("GATE_API_KEY", os.getenv("OPENAI_API_KEY", "")),
            help="API Key for gateway (default: GATE_API_KEY or OPENAI_API_KEY env)",
        )
        p.add_argument(
            "--output-dir",
            type=str,
            default="reports/triad",
            help="Directory to save individual reviews and consolidated report (default: reports/triad)",
        )

    args = parser.parse_args()

    # Model resolution: strictly from CLI flags or environment variables
    models_raw = args.models or os.getenv("TRIAD_MODELS", "") or os.getenv("GATE_MODELS", "")

    models = [m.strip() for m in models_raw.split(",") if m.strip()]
    if not models:
        print(
            "[ERROR] No models specified. Please pass --models or set TRIAD_MODELS (or GATE_MODELS) in your environment/.env.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not args.api_key:
        print("[ERROR] Missing API key. Set GATE_API_KEY in environment or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)

    print(f"\n{'='*60}")
    print(f"AJA Triad Tool: Running '{args.command.upper()}' with {len(models)} model(s)")
    print(f"Gateway: {args.gateway_url}")
    print(f"Models:  {', '.join(models)}")
    print(f"Output:   {out_dir.resolve().as_posix()}")
    print(f"{'='*60}\n")

    if args.command == "review":
        diff_text = get_git_diff(args.target)
        test_text = load_test_files(args.tests)
        user_prompt = build_review_prompt(diff_text, test_text, args.instructions)
        system_prompt = DEFAULT_SYSTEM_REVIEW_PROMPT
    else:
        spec_text = ""
        if args.spec:
            sp = Path(args.spec)
            if sp.is_file():
                spec_text = sp.read_text(encoding="utf-8")
        user_prompt = f"{args.prompt}\n\n{spec_text}".strip()
        if not user_prompt:
            print("[ERROR] Consult mode requires --prompt or --spec.", file=sys.stderr)
            sys.exit(1)
        system_prompt = DEFAULT_SYSTEM_CONSULT_PROMPT

    results = asyncio.run(
        run_triad_evaluation(
            mode=args.command,
            models=models,
            gateway_url=args.gateway_url,
            api_key=args.api_key,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            output_dir=out_dir,
            labels=[f"Triad Member {i+1}" for i in range(len(models))],
        )
    )

    consolidated_path = out_dir / f"consolidated_{args.command}_report.md"
    generate_consolidated_report(args.command, results, consolidated_path)

    print("\n" + "=" * 60)
    print(f"TRIAD {args.command.upper()} RESULTS SUMMARY")
    print("=" * 60)
    has_rejections = False
    has_low_scores = False

    for r in results:
        status_tag = "[PASS]" if r.success else "[FAIL]"
        score_tag = f"Score: {r.score:.1f}/10" if r.score is not None else "Score: N/A"
        print(f"{status_tag} {r.model} -> {r.verdict} ({score_tag}) in {r.duration_s:.1f}s")
        if r.verdict == "REJECTED":
            has_rejections = True
        if r.score is not None and args.command == "review" and r.score < args.min_score:
            has_low_scores = True

    print(f"\nConsolidated Report: {consolidated_path.resolve().as_posix()}\n")

    if args.command == "review" and args.strict:
        if has_rejections or has_low_scores:
            print("[STRICT FAILURE] One or more models rejected changes or scored below threshold.", file=sys.stderr)
            sys.exit(1)

    print("[OK] Triad evaluation completed successfully.")


if __name__ == "__main__":
    main()
