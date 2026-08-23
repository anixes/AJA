"""
AJA CLI Command: eval
=====================
Replay-based evaluation framework CLI: list cases, score missions,
and run baseline regression gates.
"""

from aja.interface.modern import console, print_error, print_info


def cmd_eval(mode: str = "run", case=None, mission_id=None, baseline=None):
    """
    `aja eval` entry point.

    Modes:
      list   — list available eval cases.
      run    — run one case against a mission_id (or replay session via --session).
      gate   — regression gate against a stored baseline JSON (default path).
    """
    from aja.evals.case import BUILTIN_CASES
    from aja.evals.runner import run_case, run_regression_gate

    mode = (mode or "run").strip().lower()

    if mode == "list":
        print_info("Available eval cases:")
        for name, c in BUILTIN_CASES.items():
            req = ", ".join(c.required_event_types) or "-"
            console.print(f"  [cyan]{name}[/] — {c.objective}")
            console.print(f"      required: {req}")
        return

    if mode == "gate":
        from pathlib import Path

        baseline_path = Path(baseline) if baseline else Path("eval_baseline.json")
        report = run_regression_gate(baseline_path)
        for e in report.entries:
            color = {"pass": "green", "new": "cyan"}.get(e.status, "red")
            base = "-" if e.baseline_score is None else f"{e.baseline_score:.2f}"
            console.print(
                f"  [{color}]{e.status:>10}[/]  {e.mission_id}  "
                f"score={e.score:.2f} baseline={base}"
            )
        if not report.entries:
            print_error("No mission journals found for regression gate.")
            raise SystemExit(1)
        if report.passed:
            print_info(f"Regression gate PASSED ({len(report.entries)} missions).")
        else:
            print_error("Regression gate FAILED: score dropped >0.2 below baseline.")
            raise SystemExit(1)
        return

    # Default: run a single case against a mission.
    if not mission_id:
        print_error("Usage: aja eval run <case> --mission <mission_id> | --baseline <path>")
        raise SystemExit(2)

    try:
        result = run_case(case, mission_id=mission_id)
    except KeyError as e:
        print_error(str(e))
        raise SystemExit(2) from e

    status = "[green]PASS[/]" if result.passed else "[red]FAIL[/]"
    console.print(f"{status}  case=[cyan]{result.name}[/]  mission={mission_id}  score={result.score:.2f}")
    for failure in result.failures:
        console.print(f"  - {failure}")
    if not result.passed:
        raise SystemExit(1)
