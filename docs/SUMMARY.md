# AgentX & AJA: The Local-First Agentic OS
**High-Performance Autonomy for Every Machine.**

## Project Mission
AgentX is engineered to run on standard hardware while delivering maximum performance. By utilizing the **Apache Arrow** memory stack and **Rust-native** acceleration, it brings elite autonomous orchestration to standard machines with a strict local-first philosophy.

## Project Milestone: "Adaptive Execution & Control"
AgentX is a self-correcting execution system with adaptive control, not yet a fully autonomous planning agent.

## Naming

- **AgentX Core**: the engine, runtime, tools, bridge, dashboard, vault, and swarm.
- **AJA**: the natural-language secretary that turns intent into explainable action.
- **AgentX Core powers AJA**.

### Components Delivered:
- **Unified CLI**: Single `agentx` command with clean subcommands (`dash`, `run`, `status`, `setup`, `doctor`, `memory`, `help`).
- **Safety Gate**: Semantic command auditing via `CommandStripper`.
- **Secret Vault**: Encrypted credential storage.
- **Unified Swarm Engine**: Replaces disjointed scripts with a single engine supporting Background, Parallel, and Baton modes.
- **API Bridge & Dashboard**: Glassmorphic real-time telemetry with secure, CSRF-protected approval routes and a Mission Launcher panel.
- **Telegram Remote Control**: AJA can receive whitelisted phone commands through Telegram Bot API -> FastAPI bridge -> AgentX Core.
- **Structured Approval Workflow**: Risky actions become approval objects with command preview, action type, reason, risk level, rollback path, expiry, requester source, and dry-run summary.
- **Structured Secretary Memory**: AJA persists obligations, follow-ups, recurring tasks, reminders, stale-task review, and escalation state in LanceDB/Arrow.
- **Messaging Layer**: AJA drafts, manages, approves, and tracks outbound communication without unsafe auto-send behavior.
- **Scheduler and Executive Reviews**: AJA generates morning, night, and weekly reviews with urgency scoring, snooze, escalation, and Telegram delivery.
- **Centralized Gateway**: Unified `UnifiedGateway` utilizing a single `providers.json` source of truth. First-class OpenRouter support.
- **Priority Engine & Decision Layer**: Multi-factor judgment scoring (urgency, stakeholder weight, consequence of delay) that ranks tasks and challenges false urgency.
- **Definition of Done (DoD) Framework**: Mandatory success criteria for all delegations, with auto-generation support for common engineering and executive tasks.
- **Executive Desk Dashboard**: Refactored command center focusing on high-level agenda and delegation oversight.
- **Resilient Recovery Layer**: LanceDB/Arrow-backed authoritative task tracking, boot-time crash recovery, and atomic tool idempotency guards.
- **Persistent Presence Loop**: Continuous AgentX loop with triggers, guardrails, health dashboard, and remote operator-in-the-loop approvals.
- **Strategy Selection Module**: Strategic dispatch layer that chooses optimal execution paths (Skill vs Compose vs Swarm) with hard risk gates and confidence fallbacks. (formerly LLM Decision Engine)
- **AJA Telegram Gateway**: Resilient, mobile-optimized communication layer with adaptive polling, table-to-bullet rendering, and vision-to-text enrichment.

### User Experience:
| What you want | What you type |
|---|---|
| Interactive shell | `agentx` |
| Launch dashboard | `agentx dash` |
| Run a mission | `agentx run [--bg] "fix all bugs"` |
| Configure API keys | `agentx setup` |
| System diagnostics | `agentx doctor` |
| Manage memory | `agentx memory list` |
| Check swarm status | `agentx status` |
| Control from phone | Telegram command to AJA |
| Vision enrichment | Send photo to Telegram |
| Add an obligation | `agentx memory add "follow up with recruiter next Tuesday"` |
| Draft communication | `agentx message draft "draft recruiter follow-up"` |
| Run executive review | `agentx review morning` |
| Ask for priority | `what should I do first` (Telegram) |

### Security Metrics:
- **Zero-Trust Input**: All intents are translated and audited before execution.
- **Memory Isolation**: Each agent runs in its own OS process via the Baton pattern.
- **Endpoint Lockdown**: Critical endpoints require Bearer Token authorization to mitigate CSRF attacks.
- **Telegram Whitelist**: Only `TELEGRAM_ALLOWED_USER_ID` can issue phone commands to AJA. Unauthorized attempts are logged and ignored.
- **Structured Operator Review**: Risky actions default to ASK and must be explainable before approval.
- **Immutable Approval Audit**: Approval lifecycle events are appended to `.agentx/approval-audit.jsonl`.
- **Encrypted Persistence**: All secrets are stored using AES-256-GCM.
- **Execution Constraints**: Mandatory Definition of Done checklists prevent "agent drift" during autonomous missions.
- **Atomic Tool Idempotency**: `ToolGuard` prevents duplicate side-effects (payments, emails) using database-level reservation locks.
- **Crash Recovery**: Auto-detection and re-queuing of interrupted tasks ensures no mission is lost to process failures.
- **LLM Decoupling**: Decision engine cannot directly execute tools or modify DB; it only selects paths for the deterministic pipeline.
- **Risk-Gated Dispatch**: HIGH risk objectives are automatically routed to ASK/REJECT by the decision engine.

## Phase 1: Telegram Remote Control

Implemented phone -> Telegram -> PC execution flow:

- `POST /telegram/webhook` receives Telegram Bot API updates.
- `POST /telegram/command` supports local testing through the same command router.
- Supported text commands: `status`, `check gpu`, `run training job`, `git pull repo`, `shutdown laptop tonight`, `restart notebook process`.
- Non-whitelisted users are denied.
- Non-text messages are rejected with a text-only explanation.
- Command history persists to `.agentx/telegram-history.jsonl`.

## Phase 2: Production Approval Workflow

Risky actions no longer rely on opaque confirmation prompts. AJA now creates a structured approval object and sends it to Telegram while syncing it to the dashboard queue through `.agentx/runtime-state.json`.

Approval objects include:

- request ID
- exact command preview
- action type
- readable reason
- risk level
- rollback path
- expiration timestamp
- requester source
- dry-run summary of expected effect

Approval commands:

- Telegram: `approve <id>` / `reject <id>`
- Dashboard: Approve / Deny buttons
- CLI/runtime: `/approve` / `/deny`

Before execution, approvals are checked for expiration and revalidated through `FileGuardian` and `CommandStripper`.

## Phase 3: Structured Secretary Memory

AJA now has a persistent LanceDB/Arrow task system at `.agent/assistant_secretary.lancedb`.

The task object supports:

- task identity, title, context, owner, due date, recurrence, priority, and status
- follow-up state, reminder state, escalation level, approval state, related people, and communication history
- source tracking across Telegram, CLI, dashboard, and system-created tasks
- scheduled review, stale task detection, priority sorting, recurring task rescheduling, and Telegram summaries

Interfaces:

- CLI: `python -m agentx memory add|list|review|complete|archive`
- FastAPI: `/memory/tasks`, `/memory/review`, `/memory/summary`
- Telegram: `tasks`, `task review`, `complete <task_id>`, `archive <task_id>`, and natural obligation messages

## Phase 4: Messaging Layer

AJA now stores outbound communication records in the same LanceDB/Arrow secretary database.

The communication object supports:

- recipient, channel, subject, draft content, and tone profile
- approval required/status
- follow-up required/due
- related task ID and communication history
- delivery status and last sent timestamp

Workflow:

```text
Draft -> Edit -> Approval -> Send -> Follow-up tracking
```

Safety rules:

- AJA never auto-sends the first version.
- All outbound messages require approval.
- Telegram is the only direct outbound adapter for now.
- Email and recruiter messages are drafted and tracked, not silently sent.

Interfaces:

- CLI: `python -m agentx message draft|list|approve|reject`
- FastAPI: `/communications`, `/communications/{message_id}/approve`, `/communications/{message_id}/send`
- Telegram: `draft recruiter follow-up`, `approve message <id>`, `send message <id>`, `check pending unanswered messages`

## Phase 5: Scheduler and Daily Executive Review

AJA now generates proactive executive reviews from structured tasks and communications.

Supported reviews:

- morning review: unfinished work, missed deadlines, urgent follow-ups, pending communication, top 3 priorities
- night review: completed work, missed commitments, ignored reminders, carry-forward actions, tomorrow focus
- weekly review: slipped commitments, stale work, blocked tasks, communication follow-ups, next-week priorities

Scheduler capabilities:

- configurable review windows
- no-spam delivery event log
- snooze
- urgency scoring
- stale task escalation
- delayed follow-up escalation
- accountability prompts
- Telegram delivery through `/scheduler/run` or `/scheduler/review/{kind}/deliver`

Interfaces:

- CLI: `python -m agentx review morning|night|weekly`
- FastAPI: `/scheduler/config`, `/scheduler/review/{kind}`, `/scheduler/run`, `/scheduler/snooze/{task_id}`
- Telegram: `morning review`, `night review`, `weekly review`, `what am I avoiding today`, `what slipped this week`

## Phase 6: Priority Engine & Definition of Done (DoD)

Assistant uses a multi-factor judgment scoring engine to rank tasks and enforces a mandatory Definition of Done (DoD) for all missions.

Capabilities:
- **Priority Scoring**: Ranks tasks (0-100) based on urgency, stakeholder weight, and consequence.
- **Urgency Challenge**: Assistant questions false urgency to prevent burnout.
- **DoD Auto-Generation**: Backend keyword matching creates success criteria for delegations.
- **Executive Desk**: Dashboard refactored to prioritize high-level agenda and oversight.

Interfaces:
- FastAPI: `/memory/priority`, `/swarm/run` (enforced DoD)
- Telegram: `what should I do first`, `what actually matters today`, `what can be ignored this week`

## Phase 7: Resilient Recovery Layer

Transforming execution from ephemeral scripts into a state-aware platform.

- **Authoritative Task Tracking**: Every mission is persisted with a `logical_task_id` and `run_id`.
- **Atomic Tool Guard**: `INSERT OR IGNORE` reservation system for production-grade idempotency.
- **Execution Coalescing**: Duplicate or retried tool calls return cached results instead of re-executing.
- **Boot-time Recovery**: `agent` automatically scans for crashed tasks on startup and resumes them.
- **Concurrent Safety**: Task-level locking prevents parallel execution collisions on the same objective.

Interfaces:
- CLI: `agentx status` (authoritative), `agentx run` (tracked)
- Persistence: `agent/persistence/tasks.py`, `agent/persistence/tools.py`, `agent/persistence/recovery.py`

## Phase 7 (AJA Gateway): Premium Secretary Integration

Porting the resilient messaging architecture from Hermes into the AJA persona.

- **Resilient Connectivity**: `TelegramAdapter` with exponential back-off and 1-hour "freshness" gates.
- **Mobile UX Enrichment**: `MobileMDRenderer` converts Markdown tables into bulleted lists for phone readability.
- **Vision Bridge**: Automatic capture and semantic description of incoming images before agent ingestion.
- **Notification Control**: Silent pings for tool progress updates; Priority pings for user-facing turns.
- **Persistent Handoffs**: Session state stored in LanceDB (`gateway_sessions.lance`) using Apache Arrow for zero-copy retrieval.
- **Security Whitelisting**: Integrated directly into `handle_gateway_event` for zero-trust message processing.
- **Auto-Config**: Zero-setup startup by reading existing `.env` secrets.

## Phase 8: Local-First "Gold Brain" Infrastructure [NEW]

The system is now capable of running entirely without cloud dependencies using the **Llama Gold** local stack.

- **Llama Gold Server**: High-performance `llama.cpp` backend configured for 50+ TPS on consumer hardware.
- **Master Launcher**: `AgentX Launcher.bat` provides a one-click initialization of the entire swarm (LLM + Bridge + Dashboard + AJA).
- **Unified Command**: `agentx.bat` provides global CLI access to the agent's core capabilities.
- **Zero-Latency Reasoning**: Optimized for real-time Telegram response generation via local inference.

Launchers:
- **`AgentX Launcher.bat`**: Master orchestrator for production startup.
- **`start_llama_gold.bat`**: Dedicated local LLM server initialization.
- **`agentx.bat`**: Global CLI shim for `agentx-core`.
- Specification: `docs/AJA_GATEWAY_SPEC.md`

## Phase 8: Resilient Skill System

Shifting from ephemeral task execution to a production-grade library of reusable, verifiable behaviors.

- **Autonomous Skill Capture**: Successful missions are "crystallized" into versioned skill records in LanceDB/Arrow.
- **Verifiable Correctness**: Post-execution assertions (postconditions) ensure results satisfy semantic requirements (e.g., file existence, specific JSON keys).
- **Multi-Skill Composition**: Heuristic splitting of complex objectives into chains with context-aware variable injection (`{{key}}`).
- **Safe Replay Engine**: Step-level recovery via checkpoints, environment prerequisite validation, and unified risk-gating.
- **Explainability & Ambiguity Resolution**: Introspection interface for skill diffs and score-proximity gating for near-identical matches.

Interfaces:
- CLI: `agent` (pre-execution recommendation), `agent status` (skill usage tracking)
- Logic: `agent/skills/skill_store.py`, `agent/skills/skill_executor.py`, `agent/skills/hierarchical_execution.py` (formerly skill_composer.py)

## Documentation Index
- [ARCHITECTURE_FLOW.md](./ARCHITECTURE_FLOW.md): Visual mapping of the system and CLI reference.
- [PHASE_1_2_REMOTE_APPROVALS.md](./PHASE_1_2_REMOTE_APPROVALS.md): Telegram control and structured approval workflow.
- [PHASE_3_SECRETARY_MEMORY.md](./PHASE_3_SECRETARY_MEMORY.md): LanceDB/Arrow-backed secretary memory.
- [PHASE_4_MESSAGING_LAYER.md](./PHASE_4_MESSAGING_LAYER.md): Outbound communication drafts and follow-up tracking.
- [PHASE_5_SCHEDULER_EXECUTIVE_REVIEW.md](./PHASE_5_SCHEDULER_EXECUTIVE_REVIEW.md): Proactive scheduler and accountability reviews.
- [PHASE_6_PRIORITY_ENGINE_DOD.md](./PHASE_6_PRIORITY_ENGINE_DOD.md): Judgment engine and mandatory delegation constraints.
- [PHASE_7_RESILIENT_RECOVERY.md](./PHASE_7_RESILIENT_RECOVERY.md): Crash survival and tool-level idempotency.
- [PHASE_8_RESILIENT_SKILLS.md](./PHASE_8_RESILIENT_SKILLS.md): Autonomous skill capture, verification, and composition.
- [PHASE_9_RESILIENT_LOOP.md](./PHASE_9_RESILIENT_LOOP.md): Persistent agent loop, triggers, guardrails, and operator-in-the-loop approvals.
- [PHASE_11_PARALLEL_SERIALIZABILITY.md](./PHASE_11_PARALLEL_SERIALIZABILITY.md): Conflict-serializable parallel execution and verification.
- [PHASE_12_SELF_IMPROVING_METHODS.md](./PHASE_12_SELF_IMPROVING_METHODS.md): Method scoring, pruning, and automated learning.
- [PHASE_14_MULTI_PLAN_VERIFICATION.md](./PHASE_14_MULTI_PLAN_VERIFICATION.md): Generate-Verify-Select architecture and Failure Memory.
- [PHASE_15_TRANSACTIONAL_REPAIR.md](./PHASE_15_TRANSACTIONAL_REPAIR.md): Transactional state, rollbacks, and localized repair engine.
- [PHASE_16_ORCHESTRATION_CAPABILITIES.md](./PHASE_16_ORCHESTRATION_CAPABILITIES.md): Capability system, sub-agents, and Agent API.
- [PHASE_21_UNCERTAINTY_PROPAGATION.md](./PHASE_21_UNCERTAINTY_PROPAGATION.md): System-wide uncertainty propagation and safety thresholds.
- [PHASE_22_SYNTHETIC_DIVERSITY.md](./PHASE_22_SYNTHETIC_DIVERSITY.md): Structural plan diversity via reasoning modes.
- [PHASE_23_MEMORY_FAILURE_INTELLIGENCE.md](./PHASE_23_MEMORY_FAILURE_INTELLIGENCE.md): Experience store and failure intelligence.
- [PHASE_24_LONG_TERM_AUTONOMY.md](./PHASE_24_LONG_TERM_AUTONOMY.md): Multi-device orchestration and goal engine.
- [PHASE_25_SELF_INITIATED_GOALS.md](./PHASE_25_SELF_INITIATED_GOALS.md): Governed autonomy and intent engine.
- [PHASE_26_RL_LITE_LEARNING.md](./PHASE_26_RL_LITE_LEARNING.md): RL-lite behavioral learning and policy store.
- [BROWSER_INTEGRATION.md](./BROWSER_INTEGRATION.md): Hybrid tiered browsing (Obscura + Vercel) and Pseudo-Snapshots.
- [HARDWARE_OPTIMIZATION.md](./HARDWARE_OPTIMIZATION.md): Universal performance engineering and Intelligent Memory architecture.
- [AGENT_ORCHESTRATION.md](./AGENT_ORCHESTRATION.md): How the multi-process swarm works.
- [AUDIT_REPORT.md](./AUDIT_REPORT.md): Historical record of surgical architectural refactoring (Phases 1-3).
- [POST_MORTEM.md](./POST_MORTEM.md): Research findings from the Claude codebase audit.

## Phase 28: Universal Hardware Optimization & Intelligent Memory

Optimizing Agent for high-performance reasoning on resource-constrained local hardware.

- **Extreme Efficiency**: Achieved industry-leading throughput by utilizing zero-copy memory patterns and native Rust execution.
- **Intelligent Memory Gate**: Implemented a 5,000-character context trigger that automatically summarizes task history to prevent the "Linear Latency Wall."
- **Hardware-Aware Gateway**: Centralized local routing that prioritizes efficient local inference while preventing accidental remote API costs.
- **Unified Launcher**: Created a single "Gold Standard" entry point for the engine and assistant services.

Interfaces:
- Launcher: `Agent Launcher.bat` (Desktop Shortcut)
- Logic: `agent/gateway.py` (Memory Monitor), `agent/orchestration/swarm.py` (Bypass Dummy)
- Docs: `docs/HARDWARE_OPTIMIZATION.md`

## Phase 9: Resilient Loop & Presence

Transitioned from one-off command execution to a robust, continuous agentic runtime.

- **Persistent Agent Loop**: A non-blocking execution engine with task prioritization (`INTERRUPTED > PENDING > FAILED`).
- **Execution Guardrails**: Integrated rate limiting, duplicate task detection, retry storm protection, no-progress stalling, and a circuit breaker that hard-stops the loop on catastrophic failure.
- **Event-driven Task Generator**: Event-driven task enqueuing supporting `TIME` (intervals), `TASK_STATE` (cascading workflows), and `FILE_FLAG` (external synchronization) with chronos-safe filtering. (formerly Trigger Engine)
- **Real-time Awareness**: Added `agent status` dashboard featuring system health indicators, load level scoring, and recent telemetry alerts.
- **Alerting & Notifications**: Telegram/CLI alerting for task completion, failures, stalls, and circuit breaker events with automated rate-limiting and duplicate collapse.
- **Operator Approval Layer**: Implementation of a pause-and-wait workflow for `HIGH` risk tasks, allowing remote `approve`/`reject`/`modify` actions and emergency CLI loop controls (`pause-loop`, `resume-loop`, `kill-task`).

Interfaces:
- CLI: `agentx run-loop`, `agentx trigger`, `agentx status`, `agentx approve/reject`, `agentx pause-loop/resume-loop`
- Logic: `agent/presence/agent_loop.py`, `agent/presence/trigger_engine.py`, `agent/presence/state.py`, `agent/presence/notifier.py`, `agent/presence/approval.py`

## Phase 10 — Execution, Control & Learning Architecture (Research-Aligned)

Agent now follows a 4-layer agent architecture aligned with modern LLM agent research:

1. **Execution Layer** (Acting)
2. **Control Layer** (Evaluation + Safety)
3. **Learning Layer** (Memory + Feedback)
4. **Routing Layer** (Pre-planning intelligence / Early planning proxy)

> [!NOTE]
> Modern LLM agents are structured around decomposition, reflection, and memory modules. In this architecture, Execution handles the acting, Control handles reflection and verification, Learning handles state and experience, and Routing acts as a lightweight proxy for planning decisions.

### Research-Aligned Module Mapping

| Internal Component | Research Taxonomy |
| :--- | :--- |
| **Skill Library** | Action Abstractions |
| **Hierarchical Task Execution** | Composition & Decomposition |
| **Strategy Selection Module** | Decision Making / Strategy Optimization |
| **Multi-Agent Evaluation / Judge Layer** | Reflection & Verification |
| **Adaptive Replanning Loop** | Self-Correction / Iterative Recovery |
| **Event-driven Task Generator** | Reactive Trigger System |

### Hierarchical Execution without Planning (Phase 10 state)

Agent currently operates as a hierarchical execution system without explicit planning.

- Tasks are executed via skills (low-level actions).
- Composition provides limited hierarchical structure.
- Strategy selection is reactive (not goal-decomposed).

> [!IMPORTANT]
> Without goal decomposition, agents remain reactive instead of autonomous. Agent is currently a self-correcting execution system with adaptive control, not yet a fully autonomous planning agent.

### Multi-Layer Evaluation Pipeline (Production-grade)

Agent implements a layered evaluation system inspired by LLM-as-judge research patterns:

- **Layer 1: Deterministic Validation** — Code-based checks (postconditions, contradictory terms).
- **Layer 2: Semantic Evaluation (LLM)** — LLM-based verification of intent satisfaction.
- **Layer 3: Multi-Evaluator Consensus** — Cross-model agreement scoring.
- **Layer 4: Meta-Evaluation (Judge-of-Judges)** — Higher-level analysis of judge reliability and bias.
- **Layer 5: Uncertainty Propagation** — Tracking confidence drift across execution.

Features include:
- **Minority Veto**: High-reliability judges can override a "Success" verdict.
- **Weighted Reliability**: Votes are weighted by historical performance in specific contexts.
- **Agreement Scoring**: Measuring consensus quality to trigger escalation.

> [!TIP]
> This aligns with reflection and evaluation modules in LLM agent taxonomy, where agents verify outputs before committing actions. Reflection and memory are core planning components in LLM agents.

### Uncertainty-Aware Execution Model

Agent treats uncertainty as a first-class control signal:

- **Step-level uncertainty tracking**: Confidence scores captured for every action.
- **Task-level accumulation with decay**: Tracking drift over time without runaway scores.
- **Hard-stop thresholds**: Halting execution when `task_uncertainty > 0.8`.
- **Propagation**: Passing uncertainty signals to the decision engine for strategy biasing.

> [!CAUTION]
> This prevents long-horizon error accumulation, a known failure mode in flat autoregressive agent systems. Flat agent policies cause error propagation in long tasks.

### Hierarchical Execution (Pre-Planning Form)

Agent partially implements hierarchical reasoning via:
- **Skill abstraction** (macro-actions)
- **Skill composition** (micro execution)
- **Retry-based local replanning**

However, global planning (task decomposition and DAG-based execution) is not yet implemented (Phase 11).

> [!NOTE]
> Hierarchical frameworks split planning into macro and micro levels. Agent currently masters the micro-level execution.

### Causal Failure Classification System

Failures are classified into deterministic causal categories:
- `AUTH_ERROR`
- `RATE_LIMIT`
- `TOOL_NOT_FOUND`
- `INVALID_INPUT`

Each category is mapped to specific recovery actions, converting repeated failures into deterministic policies and reducing repeated error loops.

### Adaptive Control & Stability Mechanisms

Execution is stabilized through several structured guards:
- **Retry Guards**: Max attempts and strategy switch limits.
- **Cascade Limits**: Preventing infinite multi-evaluator escalation.
- **Budget Control**: Hard limits on token usage and API calls.
- **Stagnation Detection**: Detecting identical outputs across retries.
- **Convergence Verification**: Independent pass to confirm goal satisfaction.

> [!NOTE]
> These controls act as execution stabilizers, preventing drift and infinite loops in long-horizon tasks. Failures often arise from lack of structured control and replanning.

### Predictive Routing Layer (Pre-Execution Intelligence)

Before execution begins, Agent estimates:
- **Task Complexity**: Based on objective length and verb count.
- **Expected Uncertainty**: Derived from complexity and historical performance.
- **Execution Cost**: Estimated token/call budget.

Based on these heuristics, it selects an optimal path:
- **Fast Path**: Single evaluator for simple tasks.
- **Cascade Path**: Multi-evaluator consensus for complex tasks.
- **Abort**: Immediate operator escalation for high-uncertainty tasks.

> [!TIP]
> This acts as a lightweight planning proxy, reducing unnecessary computation and failure risk.

## Phase 21: Uncertainty Propagation

Tracking confidence drift across execution to prevent hallucination loops.

- **Accumulated Uncertainty**: Scores are tracked per step and decayed over time.
- **Hard-stop Thresholds**: Halting execution when `task_uncertainty > 0.8`.
- **Hierarchical Awareness**: Multi-step chain validation in `hierarchical_execution.py`.

## Phase 22: Synthetic Diversity Layer

Structural diversity generation from a single model.

- **Reasoning Modes**: `DEFAULT`, `RISK_ANALYSIS`, `MINIMAL`, `AGGRESSIVE`, `SKEPTIC`.
- **Diversity Metrics**: Scoring plans based on semantic and structural variance.
- **Collapse Safeguards**: Fallback to stable execution if diversity drops.

## Phase 23: Memory & Failure Intelligence

Learning from past mistakes to bias future planning.

- **Experience Store**: Persistent memory of goal/plan/result triplets.
- **Failure Intelligence**: Penalizing plans similar to historical blunders.
- **Autonomous Scheduling**: Automated retry and maintenance tasks.

## Phase 24: Long-Term Autonomy & Multi-Device Orchestration

Evolving into a continuous, multi-environment goal system.

- **Goal Engine**: Managing objectives over long time horizons across `PENDING/DONE/FAILED`.
- **Device Routing**: Intelligent task dispatch to `Phone`, `PC`, or `Cloud`.
- **State Persistence**: Global state survives restarts via `agent_state.json`.

## Phase 25: Self-Initiated Goals (Governed Autonomy)

Proactive task generation within safety bounds.

- **Intent Engine**: Generates candidate goals from system state and user patterns.
- **Safety Filtering**: Risk/Benefit scoring and forbidden action blocking.
- **Budgeting**: Enforcing action caps and cooldown periods.

## Phase 26: RL-lite Behavioral Learning

Dynamic decision optimization via reward-based policy biasing.

- **Policy Store**: Scoring plan patterns, tools, and modes based on success.
- **Reward Function**: `Success - Latency - Repair - Risk`.
- **Exploration vs Exploitation**: Epsilon-greedy strategy for strategy discovery.
- **Drift Control**: Auto-reset of policy on success rate degradation.

## Phase 27: Hybrid Agentic Browsing & Internet Access

Equipping local, non-vision models with robust, resource-efficient internet access.

- **Dual-Engine Failover**: Tiered browser architecture using **Obscura** (Primary/Lightweight) and **Vercel Agent Browser** (Standby/Chromium).
- **Semantic Distillation**: A text-first pipeline that converts raw HTML into a structured map with element markers (`[@e1]`, `[@e2]`).
- **Pseudo-Snapshots**: Providing "textual vision" by labeling links, buttons, and inputs, allowing local models to interact with complex sites using simple numerical references.
- **Token Optimization**: Stripping 90% of noise to fit internet-scale context into limited local model windows.

Interfaces:
- Capability: `browser.search`, `browser.read`, `browser.navigate`
- Path: `E:\obscura\obscura.exe` (Primary), `agent-browser` (Standby)
- Logic: `agent/capabilities/browser.py`

## Next Evolution: Full Autonomous Deployment

Agent is now a fully autonomous learner capable of proactive self-improvement and long-horizon multi-device orchestration. Final focus remains on edge-case refinement and cross-device state synchronization stability.
