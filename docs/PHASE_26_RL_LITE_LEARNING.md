# Phase 26: RL-lite Behavioral Learning System

## Overview
Phase 26 implements a lightweight decision-optimization layer. Instead of training neural networks, Agent uses a reward-based system to bias its internal policies towards historically successful patterns.

## Core Features

### 1. RL-lite Policy Store (`policy_store.py`)
A persistent registry of "Decision Scores" for:
- **Plan Patterns**: Structures that consistently lead to success.
- **Tool Usage**: Reliability of specific capabilities in different contexts.
- **Generation Modes**: Success rates of "Risk Analysis" vs "Minimal" modes.

### 2. Reward Function
After each execution, the system computes a reward:
`Reward = Success (1.0/0.0) - Latency Penalty - Repair Penalty - Risk Penalty`
- **Positive Rewards**: Strengthen the selection probability of similar patterns.
- **Negative Rewards**: Reduce the likelihood of choosing the same tools or modes for similar goals.

### 3. Exploration vs. Exploitation
The Planner uses an **Epsilon-Greedy** strategy:
- **Exploitation**: 80% of the time, choose the highest-scored candidate plan.
- **Exploration**: 20% of the time, choose a valid but lower-scored alternative to discover better strategies.

### 4. Drift Control & Safety
- **Policy Decay**: Scores slowly revert to baseline over time (`0.99` multiplier) to stay adaptive.
- **Safety Circuit Breaker**: If success rate drops >20%, the policy is reset to prevent "Learning the wrong lesson."

---

## Technical Details
- **Persistence**: `agent_policy.json`.
- **Logic**: `agent/rl/policy_store.py` and integration in `planner.py` / `scorer.py`.
