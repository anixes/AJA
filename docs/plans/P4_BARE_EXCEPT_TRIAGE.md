# P4 Bare-Except Triage Inventory

Research date: 2026-08-26. Full classification by research agent.
Rules per agents.md: A=best-effort comment, B=add debug logging, C=real fix.
Priority dirs scanned: decision/, skills/, runtime/.

## Scope counts (all except sites, libs/aja-core/aja/)
decision/14 | skills/18 | runtime/48 | gateway/20 | orchestration/17 |
planning/15 | tui/10 | api/9 | cli/8 | utils/12 | learning/6 | interface/6 |
memory/5 | scheduler/5 | security/5 | presence/3 | embeddings/3 | goals/2 |
autonomy/2 | calendar,kernel,mcp,observability,workspace 1 each = ~216 total.
~20 files already carry best-effort comments; many already log.

## C items — REAL FIXES (15), priority order

decision/
- engine.py:43-46 — gateway init failure silently None → all decisions fallback
  w/o why. Fix: log WARNING exc_info before None.
- feedback.py:54-59 — embedding failure swaps zero-vector silently → poisoned
  retrieval. Fix: warning log; narrow exception; consider skipping vector field.

skills/
- skill_composer.py:169-172 (_skill_done) — DB fail→False→skill RE-EXECUTES.
  Fix: logger.exception then False (or fail-safe True for idempotent).
- skill_executor.py:248-259 (_load_completed_steps) — same resume-integrity;
  checkpoint read fail→{}→steps re-run. Narrow + warn.
- skill_executor.py:302-311 (_clear_checkpoints) — stale checkpoints skip steps
  on next resume. Warn.
- skill_composer.py:433-440 — evaluator fail skips uncertainty tracking →
  CHAIN_UNCERTAINTY_EXCEEDED halt-gate can't trigger. Warn.
- skill_composer.py:241-244 (validate_chain) — malformed tool_sequence JSON →
  validation SKIPPED, chain passes open. Fix: append failure to failures list.
- skill_composer.py:383-390 — context injection fail → downstream runs w/
  stale context silently. Warn.

runtime/
- broadcast.py:40-43 — delivery failures fully swallowed (+asyncio.run hides
  loop bugs). Add module logger, debug-log.
- execution/rehydrator.py:238-245 — diff parse fail→None silently; consumers
  can't tell "no diff" from "diff lost". Warn.
- execution/manager.py:451-467 (_shutdown_process) — non-timeout graceful-wait
  failures force-kill w/o diagnostics. Catch TimeoutError explicitly + warn other.
- execution/manager.py:587-590,600-617 — timeline read fails → orphan escapes
  crash-marking forever. Warn on read.
- execution/manager.py:639-643 — crash-marker write fail silent. Error log.
- handover.py:418-423 — add logger.exception before raise (unlink masks origin).
- execution/workspace.py:151-174,200-210 — git probe fails silent → weaker
  isolation fallback unexplained; _run_git "" conflates output/failure. Log.

## B items — debug logging (~12 actionable)
skills/skill_introspect.py:48-56,60-68,72-81,125-130;
skill_composer.py:89-92,199-203; skill_postconditions.py:238-246;
skill_executor.py:558-561,109-114; runtime/execution/activity.py:77-85,134-144,
194-204,30-35; runtime/execution/pty_windows.py:87-91;
runtime/execution/sequencer.py:86-89.
Already compliant (no change): runtime/events.py:120-124, event_bus.py:68-80,
sequencer.py:286-296, handover.py:450-451, decision/calibration+engine+evaluator
many sites.

## A items — comments only (~18)
autonomous_loop.py:121-124 (keep BaseException type); governance.py:138-142;
pty_posix.py:45-50,86-89,100-109(A-adjacent,B-lean),113-116,117-120;
pty_windows.py:65-68,99-102,109-113,143-146; supervisor.py:24-28,41-52,68-75;
workspace.py:40-43,111-114,131-134,117-121; manager.py:539-545;
sandbox.py:41-48; skill_executor.py:696-699.
Already commented: decision/context.py:84-91, failure_analysis.py:82-85,
engine.py:362-368.

## Cross-cutting notes
1. Some compliant sites use print() not logger (feedback.py:78,80,91,116;
   skill_executor.py:103,299) — fold into sweep.
2. ORDER MATTERS: _log_skill_status (B) writes the table _skill_done (C) reads —
   fix the C read-path FIRST or duplicate-execution risk remains regardless.

## Work order suggestion
Session 1: skills C-items (6) + decision C-items (2) — highest behavioral risk.
Session 2: runtime C-items (7). Session 3: B batch + A comments mechanical pass.
