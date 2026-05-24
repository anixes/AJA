# AJA & AJA: Capabilities Playbook

This document outlines the complete capability set of AJA Core — a research-aligned, enterprise-grade agentic orchestration system built for reliable, safe, and fully observable autonomous operations.

> **Runtime**: Always use global Python 3.12.10 at `C:\Users\Asus\AppData\Local\Programs\Python\Python312\python.exe`

---

## 🛡️ Product-Readiness Capabilities (Phase 29 — Latest)

### 1. Pydantic Configuration Validation
**Problem**: Typos or missing keys in `aja.json` caused silent runtime failures that were hard to debug.  
**Solution**: All configuration is now validated against a strict Pydantic v2 schema on every import.
- Invalid configs output clear, field-level error messages and fall back to secure defaults.
- Schema defined in `aja/config_schema.py`: `TerritoryConfig`, `SwarmModels`, `SwarmSettings`, `AgentXConfig`.
- Tests: `tests/python/test_config_validation.py`

### 2. Trace-Aware Observability (`TraceContextManager`)
**Problem**: Multi-process baton handovers and async tasks had no trace correlation — failures were impossible to attribute across workers.  
**Solution**: A contextvar-local `TraceContextManager` propagates trace IDs across threads, async tasks, and Rust-native Arrow Baton IPC.
- `get_trace_id()` / `set_trace_id()` / `TraceContextManager` in `aja/observability/telemetry.py`.
- `BatonManager` (in `aja/runtime/handover.py`) auto-embeds trace IDs in Arrow metadata headers during `capture()` and restores them during `pickup()`.
- Thread and async isolation validated in `tests/python/test_trace_telemetry.py`.

### 3. Resilient Systems Diagnostics (`aja doctor`)
**Problem**: Environment setup failures (missing keys, wrong DB schemas, unavailable Rust module) were only discovered at runtime.  
**Solution**: `aja doctor` runs a full pre-flight check of all subsystems and reports results in a formatted table.
- Checks: Config schema ✅ | Native Rust engine ✅ | LanceDB tables ✅ | API credentials ✅ | System resources ✅
- `psutil` is a **soft dependency** — gracefully falls back to `os.cpu_count()` and `shutil.disk_usage()` if not installed.
- Module: `aja/utils/diagnostics.py`

### 4. Guided Setup Wizard (`aja setup`)
**Problem**: New developers had no clear path to configure the workspace from scratch.  
**Solution**: `aja setup` walks through `aja.json` creation, `.env` key setup, and LanceDB folder initialization using interactive `rich` prompts.
- Module: `aja/main.py` (`setup` subcommand)

### 5. Safe Dry-Run Simulation (`--dry-run`)
**Problem**: Operators had no way to preview what an autonomous mission would do before committing execution.  
**Solution**: `aja run "..." --dry-run` fully simulates the mission lifecycle — planning, worker dispatch, baton creation — without executing any real commands or mutating state.
- Every command is audited via `AJAGuard` safety classification.
- Falls back to a safe simulated local plan if LLM is offline or unauthenticated.
- Module: `aja/orchestration/swarm.py`

---

## 🔬 4-Layer Agent Architecture

AJA follows a modern 4-layer agentic architecture to ensure stability and explainability:

1. **Execution Layer**: Handles raw actions via a curated **Skill Library** (Action Abstractions) and **Hierarchical Execution** (Composition).
2. **Control Layer**: Enforces reliability through a **Multi-Agent Evaluation Layer** and a **Strategy Selection Module**.
3. **Learning Layer**: Persists experience in **Vectorized Memory** and **Adaptive Replanning Loops** to self-correct failures.
4. **Routing Layer**: Performs **Predictive Routing** to choose optimal execution paths before spending tokens.

---

## 🚀 Key Capabilities

### 6. Strategy Selection Module (Decision Making)
**Problem**: Hardcoded rules fail on complex, novel tasks.  
**Solution**: The system uses a strategic dispatch layer to choose between low-level Skills, Hierarchical Composition, or Parallel Swarms. Decisions are gated by a **Rule Engine** that converts repeated failures into deterministic policies.

### 7. Multi-Agent Evaluation & Judge Layer
**Problem**: Single-model evaluation is prone to hallucinations and "Yes-man" bias.  
**Solution**: AJA uses a layered consensus pipeline:
- **Deterministic Guards**: Code-level verification of postconditions.
- **Weighted Consensus**: Votes from diverse models weighted by historical accuracy.
- **Minority Veto**: High-reliability models can override a success verdict if they detect a failure.
- **Meta-Evaluation**: The system evaluates its own judges to detect drift or bias.

### 8. Uncertainty-Aware Execution
**Problem**: Errors accumulate silently in long-horizon tasks, leading to "hallucination loops."  
**Solution**: Uncertainty is a first-class control signal.
- **Uncertainty Propagation**: Every step carries an `uncertainty_score`.
- **Compound Risk Tracking**: The system tracks drift across the entire task trajectory.
- **Hard Stop Gates**: Execution halts immediately and escalates to an operator if cumulative uncertainty exceeds safe bounds (0.8).

### 9. Predictive Routing Layer
**Problem**: Complex multi-evaluator cascades are expensive and slow for simple tasks.  
**Solution**: Before execution starts, the system analyzes the objective's complexity and uncertainty trend.
- **Fast-Path**: Simple tasks use a single evaluator to save cost and latency.
- **Cascade-Path**: High-complexity tasks are forced into the full multi-agent consensus gate.
- **Early Abstention**: If the task is predicted to be unresolvable, the system escalates to the user *before* attempting execution.

### 10. Experience-Driven Learning (RL-lite)
**Problem**: Agents often repeat the same sub-optimal patterns across different missions.  
**Solution**: AJA implements a lightweight behavioral learning layer.
- **Policy Store**: Persists success scores for plan patterns, tools, and reasoning modes.
- **Reward Optimization**: Future planning is biased toward high-reward trajectories (`Success - Latency - Risk`).
- **Failure Memory**: Plans similar to historical failures are automatically penalized, preventing recurring loops.

### 11. Governed Autonomy & Intent Generation
**Problem**: Reactive agents require constant operator prompting.  
**Solution**: The **Intent Engine** generates self-initiated goals based on system health, scheduled tasks, and user patterns.
- **Risk-Gated Autonomy**: Autonomous actions are limited by a strict safety budget and cooldown periods.
- **Benefit Scoring**: Only tasks with high predicted user value are initiated without approval.

### 12. Long-Term Multi-Device Orchestration
**Problem**: Persistent tasks get lost if the system restarts or moves between environments.  
**Solution**: The **Goal Engine** manages long-horizon objectives across Phone, PC, and Cloud.
- **Persistent State**: Goals are tracked from `PENDING` to `DONE` across system reboots.
- **Intelligent Routing**: Tasks are dispatched to the optimal hardware node based on tool requirements.

### 13. Hybrid Agentic Browsing (Internet Access)
**Problem**: Browsing is resource-intensive for local hardware, and non-vision models struggle with raw HTML noise.  
**Solution**: A tiered, text-first browsing system optimized for local inference.
- **Dual-Engine Failover**: Primary execution via **Obscura** (Rust-based, ultra-lightweight) with automatic standby transition to **Vercel Agent Browser** (Chromium-based) for complex JS-heavy sites.
- **Semantic Pseudo-Snapshots**: Automated injection of `[@eX]` markers for interactive elements, providing "textual vision" for non-multimodal models.
- **Token-Efficient Distillation**: Multi-stage cleaning pipeline that strips non-content nodes, reducing token usage by ~90% compared to raw DOM.

### 14. Hardware-Aware Memory Management (Intelligent Memory)
**Problem**: Large context windows on resource-constrained hardware lead to catastrophic latency spikes and memory crashes.  
**Solution**: A tiered, hardware-aware context monitor.
- **Automatic Summarization**: The system detects when task history exceeds **5,000 characters** and triggers a high-density compression gate.
- **Context Resetting**: Redundant logs are replaced by a "State Summary," resetting the latency trajectory while preserving critical file paths and decisions.

---

## 🛠️ Research-Aligned Workflows

| Component | Research Mapping | Focus |
| :--- | :--- | :--- |
| **Config Schema Validation** | Safety & Correctness | Fail-fast on malformed configs |
| **TraceContextManager** | Distributed Observability | Trace-aware multi-agent telemetry |
| **Systems Doctor** | DevOps Readiness | Pre-flight environment verification |
| **Dry-Run Simulation** | Safe Planning | Preview mission impact safely |
| **Skill Library** | Action Abstractions | Tool-use efficiency |
| **Hierarchical Execution** | Task Decomposition | Complex multi-step chains |
| **Strategy Selection** | Strategy Optimization | Cost/Accuracy trade-offs |
| **Multi-Agent Judge** | Reflection & Verification | Correctness guarantees |
| **Replanning Loop** | Self-Correction | Autonomous recovery |
| **Policy Store** | Decision Memory (RL) | Behavioral optimization |
| **Goal Engine** | Long-Term Planning | Multi-device persistence |

---

## 📋 CLI Quick Reference

| Command | Description |
|---------|-------------|
| `aja setup` | Interactive workspace scaffolding wizard |
| `aja doctor` | System-wide health diagnostics |
| `aja run "..."` | Dispatch mission to swarm |
| `aja run "..." --dry-run` | Safe preview simulation (no mutations) |
| `aja chat` | Interactive AJA conversational shell |
| `aja status` | Swarm health + active baton metrics |
| `aja memory list` | List persistent obligations |
| `aja memory add "..."` | Add a new obligation/follow-up |
| `aja review morning` | Generate executive morning review |
| `aja dash` | Launch React executive dashboard |

---
*Updated 2026-05-20 — Product-Readiness Phase 29 capabilities added.*
