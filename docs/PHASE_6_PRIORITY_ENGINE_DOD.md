# Phase 6: Priority Engine & Definition of Done (DoD)

## Overview
Phase 6 transforms AJA from a task tracker into an **Executive Assistant** with judgment-based prioritization and rigorous execution constraints. 

AJA now refuses to delegate work without a clear "Definition of Done," ensuring that delegated workers (Swarm Agents, AgentX Workers, etc.) have precise success criteria.

## 1. Executive Priority Engine
AJA now utilizes a multi-factor decision layer to decide what actually matters, rather than just listing tasks by date.

### Priority Scoring Dimensions (0-100)
- **Urgency**: Proximity to deadline and escalation age (how long it has been ignored).
- **Stakeholder Weight**: 
  - Recruiter / Hiring Manager: **1.5x**
  - Client / Partner: **1.3x**
  - Personal Commitment: **1.0x**
  - System Maintenance: **0.8x**
- **Consequence of Delay**: Categorized by risk (Financial, Opportunity, Trust, or Process miss).
- **Executive Intent**: Bonus points for explicitly marked "urgent" goals or repeated commitments.
- **Delegatability**: Automated judgment on whether a task should be handled by the User (Human), AJA (Secretary), or a Worker (Swarm/AgentX Worker).

### Decision Layer Outputs
Each priority item in the **Top 3 Panel** includes:
- **Priority Score**: Visual breakdown of why this task is ranked #1.
- **Urgency Challenge**: AJA explicitly asks if the task can be safely deferred (e.g., "Can this wait until Friday?").
- **Delegation Recommendation**: Advice on who should execute.

## 2. Definition of Done (DoD) Framework
AJA enforces a "Quality-First" delegation pattern. Every mission launched from the dashboard or phone must include a checklist of success criteria.

### Auto-Generation Heuristics
If no DoD is provided, AJA's backend (`api_bridge.py`) utilizes a keyword matcher to generate relevant criteria:

| Category | Generated Criteria Examples |
|---|---|
| **Code / Build** | Code reviewed, unit tests pass, no secret leakage, PR summary. |
| **Auth / Security** | E2E login works, rollback path documented, no hardcoded creds. |
| **Fix / Debug** | Root cause identified, verified with test, no regressions. |
| **Deploy / Ship** | Verified in target environment, health checks pass, rollback plan. |
| **Email / Comm** | Content approved, recipient confirmed, tone appropriate. |
| **Recruiter / Job** | Application submitted, confirmation received, follow-up set. |

### UI Integration
- **Mission Launcher**: Includes a mandatory DoD textarea (auto-populated with hints).
- **BatonBoard**: Displays active DoD checklists for all live delegations.
- **TaskBoard**: Shows DoD criteria for pending executive tasks.
- **Top 3 Panel**: Renders specific DoD items for the highest-priority work today.

## 3. Executive Desk (Dashboard Pivot)
The dashboard has been refactored from a "Swarm Console" into an "Executive Desk."

### New Primary Layout:
1. **Today’s Agenda**: The top 3 ranked executive priorities.
2. **Pending Approvals**: AJA Guard security gate and Communication drafts.
3. **Active Delegations**: The "Delegation Engine" (formerly BatonBoard) showing live workers.
4. **Communication Drafts**: Outbound messages awaiting review.
5. **System Health**: A separate submenu for technical telemetry (Swarm, Model health).

## API & Interfaces
- **FastAPI**: 
  - `GET /memory/priority`: Returns ranked tasks with scores and challenges.
  - `POST /swarm/run`: Now accepts and enforces `definition_of_done`.
- **Telegram**:
  - `what should I do first`: Returns the Top 3 ranked priorities with DoD summaries.
  - `what actually matters today`: Summarizes high-stake obligations.
  - `what can be ignored this week`: Lists low-score candidates for archiving.
