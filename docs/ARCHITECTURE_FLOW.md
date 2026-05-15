# Architecture Flow: High-Performance Local Autonomy
**AgentX Core & AJA: Built to run on standard hardware with maximum performance.**

```mermaid
graph TD
    User((User)) -->|Natural Language| AJA[AJA Secretary Persona]
    Phone((Phone)) -->|Telegram Bot API| Telegram[Telegram Webhook]
    AJA -->|Natural Language| TUI[AJA Guard TUI]
    User -->|Web Browser| Dashboard[React Dashboard]
    TUI -->|Intent Translation| Gateway[AI Gateway]
    Dashboard -->|/swarm/run| API[API Bridge]
    Dashboard -->|/config| API
    Telegram -->|Text Command| API
    Gateway -->|Tool Directive| Gate{AJA Guard}
    
    Gate -->|Allow| Engine[SwarmEngine]
    Gate -->|Ask| Approval[Structured Approval Object]
    Gate -->|Deny| Audit[Threat Log]
    Approval -->|approve/reject| Engine[SwarmEngine]
    
    Engine -->|Background Monitoring| Healer[Self Healer Agent]
    Engine -->|Parallel Batching| Worker[Parallel Agent]
    Engine -->|Objective Delegation| Worker[Baton Agent]
    
    Worker -->|Read/Write| Vault[(Secure Vault)]
    Worker -->|Repair| Code[Project Codebase]
    Worker -->|Baton Status + Lifecycle History| Batons[temp_batons/*.json]
    
    Code -->|Events| Watcher[Live Watcher]
    Watcher -->|Update| Graph[Knowledge Graph]
    
    Worker -->|Runtime State| State[.agentx/runtime-state.json]
    Worker -->|Execution History + Idempotency| Recovery[(Authoritative Recovery Memory)]
    AJA -->|Obligations + Follow-ups| Secretary[(LanceDB Secretary Memory)]
    AJA -->|Outbound Drafts| Comms[(LanceDB Communication Records)]
    Recovery -->|Task State + Tool Locks| API
    Telegram -->|Secretary Commands| Secretary
    Telegram -->|Draft / Approve / Send Message| Comms
    API -->|/memory/*| Secretary
    API -->|/communications/*| Comms
    API -->|/scheduler/*| Scheduler[Executive Scheduler]
    Scheduler -->|Read + Escalate| Secretary
    Scheduler -->|Read Followups| Comms
    Scheduler -->|Telegram Delivery| Phone
    Approval -->|Pending Approval| State
    Approval -->|Immutable JSONL| ApprovalAudit[.agentx/approval-audit.jsonl]
    Batons -->|Bridge Read + Snapshot Build| API
    State -->|Bridge Read + Snapshot Build| API
    API -->|/memory/priority| Priority[Priority Engine]
    Priority -->|Ranked Agenda| Dashboard
    Priority -->|Urgency Challenge| Dashboard
    API -->|/swarm/run + DoD| Engine[SwarmEngine]
    API -->|Read/Write| Config[.agentx/config.json]
    API -->|Approve/Deny + Mission Launch| Runner[runtime_actions.ts]
    Runner -->|Tool Execution + State Update| State
    API -->|SSE Stream| Dashboard
    API -->|Telegram Replies| Phone
```

## Naming Model

- **AgentX Core** is the engine: runtime state, tools, safety gates, dashboard bridge, vault, and swarm orchestration.
- **AJA** is the operator: the assistant personality that receives intent, explains consequences, and routes work through AgentX Core.
- Practical shorthand: **AgentX Core powers AJA**.

## Unified CLI

```
agentx              → Start the interactive AJA Guard TUI (default)
agentx dash         → Launch Dashboard + API Bridge in one command
agentx run [--bg]   → Delegate a mission to SwarmEngine (optionally in background)
agentx status       → Show swarm health & active batons
agentx explain <id> → Forensic trace of an agent's decision logic and failures
agentx metrics      → Real-time strategy success rates and reliability dashboard
agentx run-loop     → Launch the persistent autonomous agent loop
agentx trigger      → Enqueue event-driven tasks (TIME, TASK_STATE, FILE_FLAG)
agentx approve <id> → Remotely approve a pending high-risk action
agentx reject <id>  → Remotely reject a pending high-risk action
agentx setup        → Configure AI provider, API key & model interactively
agentx doctor       → Run system health checks and diagnostics
agentx memory       → Manage agent persistent memory
agentx help         → Show available commands
```

## Configuration

API settings are stored in `.agentx/config.json` and can be configured two ways:
- **CLI**: Run `agentx setup` for an interactive wizard
- **Dashboard**: Click the Settings (gear) icon in the sidebar

Both read from and write to the same config file. Gateway clients (TypeScript and Python) 
read config.json first, falling back to environment variables if the config is missing.

### Flow Breakdown:
1.  **Intent Layer**: User provides natural language via Assistant, Agent Guard TUI, Telegram, or the Dashboard's "Run Mission" input.
2.  **Priority Layer**: Intent is passed through the **Priority Engine**, which ranks tasks by urgency and stake, challenging false urgency before the user commits.
3.  **Safety Layer**: The command is stripped to its root binary by `CommandStripper`, checked for dangerous patterns by the **Assistant Guard**, and classified as **Allow / Ask / Deny**.
4.  **Approval Layer**: Risky commands pause as structured approval objects. The user sees the request ID, command preview, action type, readable reason, risk level, rollback path, expiration timestamp, requester source, and dry-run summary before approving or rejecting.
5.  **Execution Layer**: The unified `SwarmEngine` handles task execution, supporting background healing, parallel processing, and objective-based baton handoffs. Every delegation mission is constrained by a mandatory **Definition of Done (DoD)** checklist.
6.  **Feedback Layer**: Runtime events, pending approvals, approval audit records, Telegram command history, and baton task state are persisted into shared state files, then surfaced through the `API Bridge` as live SSE snapshots for the Dashboard and concise Telegram replies.

## Phase 1: Telegram Remote Control

The primary Telegram path is `UnifiedGateway` + `TelegramAdapter` (polling mode). It enforces `TELEGRAM_ALLOWED_USER_ID`, routes intents (`MISSION` / `STATUS` / `CHAT`), and forwards runtime telemetry/approval callbacks to mobile.

Bridge webhook endpoints (`/telegram/webhook`, `/telegram/command`) remain compatibility paths for legacy integrations and are not the preferred runtime control loop.

## Phase 2: Production Approval Workflow

Risky Telegram commands are written into `.agent/runtime-state.json` so the dashboard queue sees the same approval object as the phone.

All approvals expire and are re-checked through `FileGuardian` and `CommandStripper` before execution.

## Phase 3: Structured Secretary Memory

Assistant stores obligations in LanceDB at `.agent/`, separate from transient runtime state. This memory tracks obligations, follow-ups, recurring responsibilities, reminders, escalation level, communication history, and source.

## Phase 4: Messaging Layer

Assistant stores outbound communication in `secretary_communications` inside LanceDB.

Every outbound message starts as an approval-required draft. The direct send path is only implemented for Telegram, and it still refuses to send until approval is recorded.

## Phase 5: Scheduler and Executive Review

The executive scheduler reads tasks and communications from LanceDB, generates high-signal reviews, and records delivery events to prevent spam.

Supported reviews: Morning, Night, and Weekly.

## Phase 6: Priority Engine & Definition of Done (DoD)

The **Priority Engine** computes a 0-100 score for every task using urgency, stakeholder weight, and consequence. It surfaces a "Top 3" agenda on the dashboard and challenges the user with "Urgency Challenges."

The **Definition of Done (DoD)** framework enforces mandatory success criteria for every delegated mission. Criteria are auto-generated from keywords (e.g., "code", "auth", "deploy") if not manually specified, ensuring worker agents remain aligned with executive expectations.

- **Task Locking**: Prevents race conditions between concurrent agents working on the same logical task.
- **TTL Cleanup**: Automatically prunes execution logs older than 30 days.

## Phase 8: Resilient Skill System

Shifting from ephemeral task execution to a production-grade library of reusable, verifiable behaviors. 

- **Autonomous Skill Capture**: Successful missions are crystallized into versioned skill records in LanceDB.
- **Verifiable Correctness**: Post-execution assertions (postconditions) ensure results satisfy semantic requirements.
- **Multi-Skill Composition**: Split complex objectives into chains with context-aware variable injection.
- **Safe Replay Engine**: Step-level recovery via checkpoints and environment prerequisite validation.

## Phase 9: Resilient Loop & Presence

Transitioned from one-off command execution to a robust, continuous agentic runtime.

- **Persistent Agent Loop**: Non-blocking execution engine with task prioritization.
- **Execution Guardrails**: Rate limiting, duplicate task detection, retry storm protection, and circuit breakers.
- **Trigger Engine**: Event-driven task enqueuing supporting `TIME`, `TASK_STATE`, and `FILE_FLAG`.

## Phase 10: Strategy Selection Module (Research-Aligned)

Added a strategic layer to autonomously determine the optimal execution path for any objective.

- **Strategy Selection Module**: Choices between `SKILL` (Action Abstractions), `Hierarchical Execution` (Composition), `NEW`, `ASK`, or `REJECT`.
- **Gated Interaction**: Strict JSON schema validation and hard risk gates.
- **Fail-Safe Fallbacks**: Low-confidence outputs fall back to deterministic pipelines.
- **Decision Traceability**: Real-time logging of `evidence` for every decision (see `agent explain`).

## Phase 11: Vectorized Memory & Effectiveness

Enhanced the Strategy Selection Module with semantic retrieval and performance observability.

- **Vectorized Decision Memory**: Replaced keyword lookup with dense vector embeddings for semantic similarity.
- **Strategy Effectiveness Reporting**: Real-time outcome distribution metrics on the dashboard.
- **Interactive Operator-in-the-Loop**: Responsive CLI prompts for `ASK` strategies.

## Phase 12-14: Causal Rules, Metrics & Stability

- **Causal Rule Engine**: Automatically converts repeated failures into targeted, actionable rules (e.g., `AUTH_ERROR → ASK`).
- **Decision Quality Metrics**: LanceDB-backed metrics layer computes accuracy, failure rates, and retry success rates.
- **Loop Stability (Convergence)**: Stagnation and trend analysis to prevent "hallucination finish" and infinite loops.

## Phase 15-17: Final Hardening & Control Layer

- **Evaluator Calibration**: routinely replays "Golden Tasks" to detect evaluator drift.
- **Step-level Evaluation**: Validates every step of a `Hierarchical Execution` chain, aborting on failure.
- **Multi-Evaluator Pipeline**: 4-layer consensus system (Deterministic -> Multi-Eval -> Minority Veto -> Agreement).
- **Cost-Optimized Verification**: Intelligent skipping of expensive multi-eval for low-risk, high-confidence tasks.

## Phase 18-19: Risk-Aware Correctness & Reliability

- **Probabilistic Risk Scoring**: Evaluation results include a `risk_score` (0.0-1.0) and `veto_triggered` flag. High-risk convergence triggers mandatory operator escalation.
- **Reliability-Weighted Multi-Eval**: Evaluator signals are weighted by historical accuracy. Weak judges (< 0.3 reliability) are suppressed; high-reliability judges can issue **HARD VETO** signals.
- **Meta-Evaluation Layer**: Autonomous calibration against "Golden Tasks" detects biased or inconsistent judges and alerts on performance drift.
- **Diversity Guardrails**: Enforces consensus across diverse model families (e.g., GPT, Claude, Gemini) to prevent correlated "yes-man" failures.
