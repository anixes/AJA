You are a test-triage worker for the AJA repo at D:\AgenticAI\Project1(no-name).

TASK:
1. Run the full test suite: `py -3.12 -m pytest tests/python -n 4 --dist loadgroup --timeout=420 -q -p no:cacheprovider` (workdir = repo root).
2. For EVERY failed test, re-run it alone (`py -3.12 -m pytest "<test>" -q --timeout=120 -p no:cacheprovider`) and classify:
   - HARDWARE-NOISE: interpreter aborts, Rust allocation failures, PyO3 panics, or passes in isolation after an abort
   - FLAKE: passes when re-run in isolation
   - REAL-BUG: fails consistently in isolation
3. For REAL-BUG classifications only: include the first 10 lines of the failure output. DO NOT modify any code.

OUTPUT: write a markdown report to .opencode/night-shift/agy/test-triage.md with:
- Summary line (total passed/failed/skipped)
- Table: test id | classification | evidence (1 line)
- A "Real bugs" section (or "None found")

RULES: never run pytest with -n greater than 4. Never modify production code. If the suite aborts the interpreter, note where it died and continue triaging what you can.
