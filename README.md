# AJA on AgentX Core

**AgentX Core** is the high-performance, security-first orchestration engine.
**AJA** is the assistant personality and operator that uses AgentX Core to plan, execute, and supervise work.

In short: **AgentX Core powers AJA**.

---

## Key Pillars

### 1. Self-Healing Swarm
A decentralized network of agents that monitor your territories (`src/prod`, `src/vault`, etc.). If a logic bug or crash is detected, the Swarm automatically diagnoses the error via the **AI Gateway** and applies a verified patch.

### 2. The Secure Vault (AES-256-GCM)
A military-grade secret management system. Agents can retrieve deployment tokens and API keys privately in-process, ensuring credentials never leak into chat logs or terminal history.

### 3. SafeShell (Safety Gate)
A tiered risk auditing system that intercepts every bash command. It classifies commands as **Allow / Ask / Deny**, pauses risky operations as structured approval requests for CLI, dashboard, or Telegram review, and blocks dangerous patterns like `curl | bash`.

### 4. Executive Desk (Dashboard)
A premium React + Vite command center focused on high-level operator visibility. It prioritizes **Today’s Agenda**, **Pending Approvals**, and **Active Delegations**. Technical swarm telemetry is secondary, ensuring the user stays focused on executive decisions.

### 5. Telegram Remote Control
AJA can receive whitelisted phone commands through Telegram, route them through AgentX Core safety checks, and return concise mobile-readable output.

### 6. Production Approval Workflow
Risky actions (Shell commands or outbound messages) become structured approval objects with risk levels, rollback paths, and dry-run summaries. Every delegation mission requires a mandatory **Definition of Done (DoD)** before worker dispatch.

### 7. Structured Secretary Memory
AJA persists obligations, follow-ups, recurring responsibilities, reminders, and accountability commitments in SQLite so they survive restarts and can be reviewed from CLI, dashboard API, or Telegram.

### 8. Messaging Layer
AJA drafts, edits, approves, and tracks outbound communication without auto-sending first versions. Recruiter follow-ups, reminders, professional replies, and accountability check-ins are stored in SQLite with follow-up tracking.

### 9. Priority Engine & Executive Reviews
AJA uses a multi-factor **Judgment Engine** to score tasks by urgency, stakeholder weight, and consequence of delay. It generates morning, night, and weekly executive reviews, challenges false urgency, and suggests tasks that can be safely ignored.

### 10. Controlled Agent Verification & Worker Registry
AJA manages a registry of specialist workers (Copilot, Gemini, Aider, etc.) and executes delegated missions with strict **Definition of Done (DoD)** enforcement. Every worker output is independently audited by the **Verification Engine** for test integrity, branch isolation, and secret leakage before human merge approval is permitted.
### 11. Parallel Plan Serializability & Verification
AJA implements a conflict-aware scheduler that decomposes complex objectives into parallel "waves" of execution. The **Serializability Verification Layer** ensures that concurrent execution is mathematically equivalent to a safe sequential baseline, preventing race conditions and state corruption during high-throughput autonomous missions.

### 12. Autonomous Strategy System (Phase 27)
AgentX now operates on a **Think-Simulate-Act** loop. It generates multiple plans, simulates their outcomes in an internal world model, and selects the optimal strategic path based on predicted risk, success probability, and latency.

### 13. Self-Generated Curriculum & Evolution
The system autonomously detects skill gaps in its own performance and generates synthetic practice tasks in a controlled sandbox. This enables continuous strategic improvement and tool mastery without human intervention.

### 14. Dynamic Critic & Calibration Layer (Phase 21.6)
AJA continuously evaluates generated execution plans through an LLM-enhanced reasoning critic. The engine features dynamic confidence thresholding, calibrating its strictness autonomously based on observed false positive/negative rates, and detects "shared reasoning errors" to prevent false consensus across the swarm.

---

## Priority Engine
The **Priority Engine** is the core logic that prevents "agent drift." It cross-references current tasks against your **Strategic North Star** (a persistent context file). It filters the swarm's activity to prioritize high-leverage outcomes, preventing the system from wasting tokens on low-value optimizations while critical deadlines loom.

---

## Technology Stack
- **Backend**: Node.js (TypeScript), Python 3.12 (FastAPI/Textual)
- **Frontend**: React 19, Framer Motion, Tailwind CSS, Vite
- **Security**: AES-256-GCM, Zod Validation, Custom Command Stripper
- **Orchestration**: Baton-Handoff Pattern (Multi-Process isolation)
- **Local AI Engine**: llama.cpp (CUDA optimized)

---

## Local AI Configuration (Optimized for 4GB VRAM)

AgentX Core is optimized to run AJA fully offline using **llama.cpp**. For hardware with 4GB VRAM (e.g., GTX 1650 Ti), we use a multi-model "Swarm" approach:

- **Brain (Planner/Critic)**: `phi4-mini` (3.8B, Q4_K_M) - High reasoning, 2.5 GB.
- **Hands (Worker)**: `qwen2.5:3b` (3B, Q4_K_M) - Coding specialist, 1.9 GB.

### Model Performance
Run the benchmark to verify GPU acceleration:
```powershell
python scripts/performance_test.py
```
*Goal: >20 TPS for real-time agentic swarms.*

## Quick Start

### 1. Setup Environment
```bash
npm install
cd dashboard && npm install
```

### 2. Launch the Ecosystem
Run the unified command to start the API Bridge and the Visual Command Center:
```bash
agentx dash
```


### Telegram Remote Control
AJA can accept Telegram text commands through the AgentX Core FastAPI bridge. Set these environment variables before starting `scripts/api_bridge.py`:
```bash
TELEGRAM_BOT_TOKEN=123456:bot-token
TELEGRAM_ALLOWED_USER_ID=123456789
TELEGRAM_WEBHOOK_SECRET=long-random-secret
```

Expose the bridge to Telegram and register the webhook:
```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook?url=https://YOUR_PUBLIC_URL/telegram/webhook&secret_token=$TELEGRAM_WEBHOOK_SECRET"
```

Supported commands: `status`, `check gpu`, `run training job`, `git pull repo`, `shutdown laptop tonight`, and `restart notebook process`. Risky commands create structured approval requests with `approve <id>` / `reject <id>`. History is persisted in `.agentx/telegram-history.jsonl`; immutable approval audit entries are persisted in `.agentx/approval-audit.jsonl`.

Secretary commands: `tasks`, `task review`, `complete <task_id>`, `archive <task_id>`, or natural obligations like `follow up with recruiter next Tuesday`.

Messaging commands: `draft recruiter follow-up`, `draft professional reply to recruiter`, `remind Rahul about project deadline`, `approve message <message_id>`, `send message <message_id>`, and `check pending unanswered messages`.

Executive review commands: `morning review`, `night review`, `weekly review`, `what am I avoiding today`, `what slipped this week`, `snooze <task_id> tomorrow`, `what should I do first`, `what actually matters today`, and `what can be ignored this week`.

### 3. CLI Missions
Use the CLI for autonomous planning:
```bash
agentx run "Build a new module for X"
```


---

## Project Structure
- `agentx/`: Core engine logic and CLI entry points.
- `scripts/`: Supplemental AI agents, health checks, and API bridges.
- `.agentx/runtime-state.json`: Shared AgentX Core runtime state consumed by AJA and the dashboard API bridge.

- `.agentx/aja_secretary.sqlite3`: SQLite secretary memory for AJA obligations and follow-ups.
- `src/runtime_actions.ts`: Dashboard-triggered approve/deny action runner for pending runtime approvals.
- `src/telegram_file_guardian_check.ts`: Adapter that lets the Telegram bridge route command previews through FileGuardian.
- `.agentx/approval-audit.jsonl`: Append-only approval lifecycle audit log.
- `src/vault/`: Cryptographic core and storage logic.
- `dashboard/`: The Visual Command Center (React).
- `graphify-out/`: Live-updated Knowledge Graph of the codebase.

---

## Philosophy
AgentX Core is built on the principle that **autonomous agents must be constrained by human security patterns**. AJA is the operator on top: expressive, useful, and accountable to the human approval loop.
