# Phase 25: Self-Initiated Goals (Controlled Autonomy)

## Overview
Phase 25 introduces **Governed Autonomy**, allowing Agent to initiate its own actions based on system state, user patterns, and scheduled maintenance requirements.

## Core Features

### 1. Intent Engine (`intent_engine.py`)
Generates candidate goals independently by monitoring:
- **Scheduled Tasks**: Maintenance, backups, health checks.
- **Recent Failures**: Proactive retries or diagnostic tasks.
- **User Patterns**: Anticipating needs based on historical usage.
- **System State Changes**: Reacting to low disk space, high CPU, or network issues.

### 2. Intent Scoring & Filtering
Autonomous goals must pass a strict "Benefit vs Risk" filter.
- **Scoring**: Goals are ranked by predicted user value and system safety.
- **Constraints**: Maximum autonomous actions per hour and mandatory cooldown periods.
- **Forbidden Actions**: Destructive actions (e.g., `delete_files`) are hard-blocked from self-initiation.

### 3. Operator-in-the-Loop (OITL) Safety
High-risk autonomous goals (Score > 0.7) trigger a Telegram approval request before execution. "Stable" mode acts as a fallback if autonomy shows high drift.

---

## Technical Details
- **Engine**: `agent/autonomy/intent_engine.py`.
- **Budgeting**: Enforced via `MAX_AUTONOMOUS_ACTIONS` and `COOLDOWN_PERIOD` in `config.py`.
