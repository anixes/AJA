# Agent: Adaptive Execution & Control Playbook

This document outlines the advanced capabilities of Agent Core, a research-aligned agentic system built for reliable, autonomous terminal operations.

## 🔬 4-Layer Agent Architecture

Agent follows a modern 4-layer agentic architecture to ensure stability and explainability:

1.  **Execution Layer**: Handles raw actions via a curated **Skill Library** (Action Abstractions) and **Hierarchical Execution** (Composition).
2.  **Control Layer**: Enforces reliability through a **Multi-Agent Evaluation Layer** and a **Strategy Selection Module**.
3.  **Learning Layer**: Persists experience in **Vectorized Memory** and **Adaptive Replanning Loops** to self-correct failures.
4.  **Routing Layer**: Performs **Predictive Routing** to choose optimal execution paths before spending tokens.

---

## 🚀 Key Capabilities

### 1. Strategy Selection Module (Decision Making)
**Problem**: Hardcoded rules fail on complex, novel tasks.
**Solution**: The system uses a strategic dispatch layer to choose between low-level Skills, Hierarchical Composition, or Parallel Swarms. Decisions are gated by a **Rule Engine** that converts repeated failures into deterministic policies.

### 2. Multi-Agent Evaluation & Judge Layer
**Problem**: Single-model evaluation is prone to hallucinations and "Yes-man" bias.
**Solution**: Agent uses a layered consensus pipeline:
*   **Deterministic Guards**: Code-level verification of postconditions.
*   **Weighted Consensus**: Votes from diverse models (GPT-4o, Gemini 1.5, Claude 3.5) weighted by historical accuracy.
*   **Minority Veto**: High-reliability models can override a success verdict if they detect a failure.
*   **Meta-Evaluation**: The system evaluates its own judges to detect drift or bias.

### 3. Uncertainty-Aware Execution
**Problem**: Errors accumulate silently in long-horizon tasks, leading to "hallucination loops."
**Solution**: Uncertainty is a first-class control signal.
*   **Uncertainty Propagation**: Every step carries an `uncertainty_score`.
*   **Compound Risk Tracking**: The system tracks drift across the entire task trajectory.
*   **Hard Stop Gates**: Execution halts immediately and escalates to a operator if cumulative uncertainty exceeds safe bounds (0.8).

### 4. Predictive Routing Layer
**Problem**: Complex multi-evaluator cascades are expensive and slow for simple tasks.
**Solution**: Before execution starts, the system analyzes the objective's complexity and uncertainty trend.
*   **Fast-Path**: Simple tasks use a single evaluator to save cost and latency.
*   **Cascade-Path**: High-complexity tasks are forced into the full multi-agent consensus gate.
*   **Early Abstention**: If the task is predicted to be unresolvable, the system escalates to the user *before* attempting execution.

### 6. Experience-Driven Learning (RL-lite)
**Problem**: Agents often repeat the same sub-optimal patterns across different missions.
**Solution**: Agent implements a lightweight behavioral learning layer.
*   **Policy Store**: Persists success scores for plan patterns, tools, and reasoning modes.
*   **Reward Optimization**: Future planning is biased toward high-reward trajectories (`Success - Latency - Risk`).
*   **Failure Memory**: Plans similar to historical failures are automatically penalized, preventing recurring loops.

### 7. Governed Autonomy & Intent Generation
**Problem**: Reactive agents require constant operator prompting.
**Solution**: The **Intent Engine** generates self-initiated goals based on system health, scheduled tasks, and user patterns.
*   **Risk-Gated Autonomy**: Autonomous actions are limited by a strict safety budget and cooldown periods.
*   **Benefit Scoring**: Only tasks with high predicted user value are initiated without approval.

### 8. Long-Term Multi-Device Orchestration
**Problem**: Persistent tasks get lost if the system restarts or moves between environments.
**Solution**: The **Goal Engine** manages long-horizon objectives across Phone, PC, and Cloud.
*   **Persistent State**: Goals are tracked from `PENDING` to `DONE` across system reboots.
*   **Intelligent Routing**: Tasks are dispatched to the optimal hardware node based on tool requirements.

### 9. Hybrid Agentic Browsing (Internet Access)
**Problem**: Browsing is resource-intensive for local hardware, and non-vision models struggle with raw HTML noise.
**Solution**: A tiered, text-first browsing system optimized for local inference.
*   **Dual-Engine Failover**: Primary execution via **Obscura** (Rust-based, ultra-lightweight) with automatic standby transition to **Vercel Agent Browser** (Chromium-based) for complex JS-heavy sites.
*   **Semantic Pseudo-Snapshots**: Automated injection of `[@eX]` markers for interactive elements (links, buttons, inputs), providing "textual vision" for non-multimodal models.
*   **Token-Efficient Distillation**: Multi-stage cleaning pipeline that strips non-content nodes, reducing token usage by ~90% compared to raw DOM.

### 10. Hardware-Aware Memory Management (Intelligent Memory)
**Problem**: Large context windows on resource-constrained hardware lead to catastrophic latency spikes and memory crashes.
**Solution**: A tiered, hardware-aware context monitor.
*   **Automatic Summarization**: The system detects when task history exceeds **5,000 characters** and triggers a high-density compression gate.
*   **Context Resetting**: Redundant logs are replaced by a "State Summary," resetting the latency trajectory while preserving critical file paths and decisions.
*   **Performance Tuning**: The system is tuned for high-throughput prompt processing, utilizing optimized batching profiles to ensure rapid reasoning without over-committing local resources.

---

## 🛠️ Research-Aligned Workflows

| Component | Research Mapping | Focus |
| :--- | :--- | :--- |
| **Skill Library** | Action Abstractions | Tool-use efficiency |
| **Hierarchical Execution** | Task Decomposition | Complex multi-step chains |
| **Strategy Selection** | Strategy Optimization | Cost/Accuracy trade-offs |
| **Multi-Agent Judge** | Reflection & Verification | Correctness guarantees |
| **Replanning Loop** | Self-Correction | Autonomous recovery |
| **Policy Store** | Decision Memory (RL) | Behavioral optimization |
| **Goal Engine** | Long-Term Planning | Multi-device persistence |

---
*Updated via Phase 26 Hardening on 2026-05-02.*
