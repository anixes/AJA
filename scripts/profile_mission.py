"""cProfile wrapper for AJA hot paths.

Usage:
    py -3.12 scripts/profile_mission.py [--objective "text"] [--top 25]
        [--target registry|journal|embedding|swarm]

Default target runs the registry+journal+embedding micro-suite, which requires
NO network or LLM access. ``--target swarm`` profiles the SwarmEngine dry-run
planning path (plan_and_execute_batons) and DOES require a configured LLM key.
"""

import argparse
import asyncio
import cProfile
import io
import os
import pstats
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "libs" / "aja-core"))

# Keep profiling local + deterministic: no shared state, mock embeddings.
_isolated = Path(tempfile.gettempdir()) / "aja_profile_run" / "data"
_isolated.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("AJA_DATA_DIR", str(_isolated))
os.environ.setdefault("AJA_MOCK_EMBEDDINGS", "1")
os.environ.setdefault("AJA_TRACE_DIR", str(Path(tempfile.gettempdir()) / "aja_profile_run" / "traces"))


def _profile(label: str, fn, top: int) -> None:
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    finally:
        profiler.disable()

    out = io.StringIO()
    stats = pstats.Stats(profiler, stream=out)
    stats.strip_dirs().sort_stats("cumulative").print_stats(top)
    print(f"\n{'=' * 78}\nPROFILE: {label}\n{'=' * 78}")
    print(out.getvalue())


def target_registry() -> None:
    from aja.orchestration.tools.native import NativeToolRegistry

    registry = NativeToolRegistry()

    def run() -> None:
        for _ in range(200):
            registry.execute("get_datetime", {})
            registry.execute("__no_such_tool__", {})

    return _profile("NativeToolRegistry.execute dispatch (400 calls)", run, TOP)


def target_journal() -> None:
    from aja.runtime import mission_journal as mj_module

    journal = mj_module.MissionJournal("profilerun")

    def run() -> None:
        for i in range(30):
            journal.emit("MISSION_STATUS_CHANGED", {"to": "ACTIVE", "i": i})

    return _profile("MissionJournal.emit x30 (append + projection rebuild)", run, TOP)


def target_embedding() -> None:
    from aja.memory.territory import get_text_embedding

    def run() -> None:
        for i in range(500):
            get_text_embedding(f"profile embedding throughput sample {i}")

    return _profile("get_text_embedding x500 (mock hash path)", run, TOP)


def target_swarm(objective: str) -> None:
    from aja.orchestration.swarm import SwarmEngine

    engine = SwarmEngine(dry_run=True)

    return _profile(
        f"SwarmEngine.plan_and_execute_batons (dry-run): {objective!r}",
        engine.plan_and_execute_batons(objective),
        TOP,
    )


TOP = 25

TARGETS = {
    "registry": lambda obj: target_registry(),
    "journal": lambda obj: target_journal(),
    "embedding": lambda obj: target_embedding(),
    "swarm": target_swarm,
}


def main() -> int:
    global TOP
    parser = argparse.ArgumentParser(description="Profile AJA mission/hot-path components.")
    parser.add_argument("--objective", default="Perform project analysis", help="Mission text for --target swarm")
    parser.add_argument("--top", type=int, default=25, help="Number of pstats rows to print")
    parser.add_argument(
        "--target",
        choices=sorted(TARGETS),
        default=None,
        help="Component to profile; omit to run the full no-LLM micro-suite (registry+journal+embedding)",
    )
    args = parser.parse_args()
    TOP = args.top

    selected = [args.target] if args.target else ["registry", "journal", "embedding"]
    for name in selected:
        TARGETS[name](args.objective)
    return 0


if __name__ == "__main__":
    sys.exit(main())
