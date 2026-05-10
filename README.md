# AgentX

## 🚀 Local Performance (GTX 1650 Ti Optimized)

This repository is tuned for high-performance local inference on 4GB VRAM hardware.

### Quick Start
1.  **Launch**: Double-click the **AgentX** icon on your desktop (or run `AgentX Launcher.bat` as Administrator).
2.  **Performance**: Achieves **~350 t/s** prompt processing and **~88 TPS** generation using the Gold Standard server configuration.
3.  **Intelligent Memory**: Context is automatically summarized when it exceeds 5,000 characters to maintain constant low-latency reasoning.

### Performance Tunables
*   **Context Limit**: 32,768 tokens (Locked for stability).
*   **Batching**: `ubatch=256` (Maximized for 1650 Ti compute cores).
*   **Offline Mode**: Enabled by default in `agentx.json` to prioritize local Llama.cpp.

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
- **Core Engine**: Python 3.12 (Modular Package)
- **CLI App**: TypeScript / Node.js (Ink-based)
- **Dashboard**: React 19, Framer Motion, Tailwind CSS, Vite
- **Security**: AES-256-GCM, Zod Validation, Custom Command Stripper
- **Orchestration**: Baton-Handoff Pattern (Multi-Process isolation)
- **Local AI Engine**: llama.cpp (CUDA optimized)

---

## Quick Start

### 1. Setup Environment
```bash
npm install
```
*Note: This will install dependencies for all workspaces (apps and packages).*

### 2. Launch the Ecosystem (AJA Stack)
Run the unified command to start the API Bridge (Python), the Telegram Poller, and the Visual Command Center (Vite):
```bash
npm run aja
```

### 3. Telegram Interaction
AJA now uses a **robust local polling loop**. No webhooks are required, making it perfect for local development. 
- Ensure `TELEGRAM_TOKEN` and `TELEGRAM_ALLOWED_USER_ID` are set in `.env`.
- Message your bot directly. Try `status` or `gpu`.

### 3. CLI Missions
Use the CLI for autonomous planning:
```bash
agentx run "Build a new module for X"
```

---

## Project Structure

The project follows a modern **Apps/Packages Monorepo** architecture:

- **`apps/`**
  - `cli-ts/`: TypeScript-based terminal application and simulation tools.
  - `dashboard/`: Premium React + Vite Visual Command Center.
- **`packages/`**
  - **`agentx-core/`**: The main Python orchestration engine.
    - `agentx/`: Core module logic (agents, goals, memory, security, utils).
- **`tests/`**: Unified testing environment for both Python and TypeScript suites.
- **`.agentx/`**: Centralized runtime state, secretary memory (SQLite), and audit logs.
- **`agentx.bat`**: Unified launcher for the entire ecosystem.

---

## Philosophy
AgentX Core is built on the principle that **autonomous agents must be constrained by human security patterns**. AJA is the operator on top: expressive, useful, and accountable to the human approval loop.
