# Phase 24: Long-Term Autonomy + Multi-Device Orchestration

## Overview
Phase 24 evolves Agent into a continuous, goal-driven system that manages objectives across multiple devices (Phone, PC, Cloud) over long time horizons.

## Core Features

### 1. Goal Engine (`goal_engine.py`)
A centralized engine for managing long-term objectives.
- **Goal Lifecycle**: Tracks `PENDING`, `RUNNING`, `DONE`, and `FAILED` states.
- **Decomposition**: Uses the Planner to break high-level goals into executable subgoals.
- **Persistence**: Goals survive system restarts via `agent_state.json`.

### 2. Multi-Device Routing
The system can now route task execution to the most appropriate node:
- **Phone**: User interaction, mobile-specific apps.
- **PC**: Local development, file system operations.
- **Cloud**: Heavy compute, long-running scrapes, 24/7 monitoring.
- **Routing Logic**: Derived from tool metadata and capability availability.

### 3. Continuous Autonomy Loop
A background loop that constantly evaluates the goal queue, prioritizes tasks based on deadlines and user benefit, and manages concurrent execution waves.

---

## Technical Details
- **Goal Management**: `agent/goals/goal_engine.py`.
- **Remote Control**: Expanded Telegram interface for adding, pausing, and status-checking long-term goals.
