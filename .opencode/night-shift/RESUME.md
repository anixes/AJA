# Night Shift — RESUME CHECKPOINT
# Updated: 2026-08-25 ~16:45 local — WAVES 0-3 COMPLETE
# MACHINE: RAM instability CONFIRMED via WinDbg (0x10e_2d dxgmms2!VIDMM_CPU_HOST_APERTURE::MapRange).
#   Memory Diagnostic SCHEDULED (bcdedit /bootsequence {memdiag}) - runs on NEXT reboot.
#   Suspect: non-Samsung "0949" OEM stick. Suite failures that abort the interpreter
#   (Rust allocation failures / PyO3 panics) = hardware noise; verify in isolation.

## PUSHED STATE (origin/native-worker-3, newest last)
- dfbf1bc wave-0: Telegram vision + single-instance daemons + heartbeat fix
- 5beffef wave-1: async/type sweep (~50 fixes)
- 384d2be + df40179 wave-2 E1: CommandGuard newline-chaining + interpreter laundering
  (calibrated: separator-free read-only pipelines keep fast path)
- 72181ce wave-2 E6: providers (anthropic base_url, copilot env-token preservation,
  tool-name sanitization bijection, array-root schema wrap)
- bfb6cbd wave-2 E2: bridge auth (loopback-default bind, protected routes,
  atomic approvals, telegram token redaction)
- 2b5f35a: ws broadcasting test authenticates
- 9b20510 wave-3a: activity_rt off-loop tools + get_runtime_events reader +
  AJA_BRIDGE_BACKGROUND_DISABLED opt-out (maintenance thread + telegram poller)
- 9242f68 wave-3b: telegram poll supervisor (restart w/ backoff) +
  approval_expires_at stamping at all 3 emission sites
- bcf95dc wave-3c: chat_stream mid-stream failure no longer duplicates replies
- 0b0f6c3 wave-3d: Discord bus subscription at start (standalone gateway had ZERO Discord telemetry)
- 150e944 wave-3e: dashboard _tick_spinner mount/unmount race guarded (rotating flake FIXED)
- 7009c39 + fd1ccb9 wave-3f: shared runtime sink in scheduler.telegram
- Final gate: 1241 passed, 1 timing-flake (passes in isolation)
- E3 approvals / E4 streaming / E5 llm-semantics / E7 orchestrator fixes are inside
  the bfb6cbd..9b20510 commits (ledger reports in .opencode/night-shift/ledger/)

## REMAINING (Wave 4-5 backlog; details in briefs/ + ledger deferrals)
- Slack telemetry dispatcher (G3#3): needs fan-out wiring like tg_client tails
- gateway_runner explicit-responder refactor (E1-deferred; replaces adapter-swap lock)
- async decompose refactor (E5-deferred); briefing/rebuild offload
- Architecture items from explore agents: episodic memory is keyword-JSON not vectors;
  4 overlapping task stores; agent_memory table orphaned
- Morning checklist: 1) let Memory Diagnostic run on reboot, note results
  2) retest Telegram product-photo advice 3) RAM stick swap decision

## HARD RULES
- pytest -n 2 scoped / -n 4 gates (RAM instability under sustained load)
- Never restart the live gateway/worker processes without user say-so
- git checkout libs/aja-core/data/failures.json before every commit
- Interpreter-aborting failures (Rust alloc/PyO3 panic) = hardware noise; verify in isolation
