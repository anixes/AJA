# AJA Runtime Console Expansion — Conversational Assistant & HTN Plan Tree DAG

This plan expands the AJA Durable Execution & Swarm Runtime web console with two flagship operator capabilities from the AJA architecture: an interactive **Conversational AJA Chat Assistant** supporting full slash-command workflows (`/swarm`, `/goal`, `/todo`, `/doing`, `/done`, `/failed`, `/kanban`, `/status`, `/doctor`, `/models`, `/clear`) and an **Interactive HTN (Hierarchical Task Network) Plan Tree DAG Viewer** with live node state inspection, dry-run execution simulation, and Operator-in-the-Loop (OITL) node approval/modification.

## User Review & Critical Decisions

> [!IMPORTANT]
> The decisions below reflect your confirmed selections from the clarification step and establish the architecture for the expanded AJA console.

- **Confirmed Decision 1 — Primary Focus**: Expand AJA dashboard features and interactive tools while preserving all existing runtime bridge endpoints and in-memory state stores.
- **Confirmed Decision 2 — New Capabilities**:
  1. **Conversational AJA Chat with Slash Commands**: Interactive command & chat terminal with slash-command autocomplete, Kanban task mutations, system diagnostics (`/doctor`), and server-side Gemini (`gemini-3.8-flash`) conversational fallback.
  2. **Interactive HTN Plan Tree DAG Viewer**: Visual hierarchical task network graph showing root missions, decomposed methods, and leaf activity nodes with real-time status transitions (`PENDING`, `RUNNING`, `WAITING_APPROVAL`, `COMPLETED`, `FAILED`), trace lineage, and node-level OITL controls (`/hitl/approve`, `/hitl/reject`, `/hitl/modify_node`).

---

## 1. Overview & Core Concept

- **What It Does**: Transforms the AJA Runtime console into a unified multi-view operator workspace combining the **Executive Priority Desk**, **HTN Plan Tree DAG Inspector**, **Conversational AJA Chat & Kanban Studio**, and **Swarm Worker & Safety Governance**.
- **Target Audience / Persona**: Systems engineers and autonomous swarm operators who need deterministic visibility into hierarchical task plans, durable activity journals, and rapid slash-command orchestration.
- **Key Value**: Bridges conversational intent parsing and structured slash commands directly with deterministic HTN plan decomposition, Arrow baton handovers, and Operator-in-the-Loop governance.

---

## 2. User Experience & Visual Design

- **Key User Flows**:
  1. **Workspace Navigation**: A clean 3-zone top bar and left sidebar allow operators to switch seamlessly between *Executive Priority Desk*, *HTN Plan Tree DAG*, *AJA Chat & Slash Console*, and *Workers & Event Journal*, or view them in a coordinated split layout on 1440px desktop viewports.
  2. **Interactive HTN Plan Tree DAG Flow**:
     - Operators select an active mission or generate a new HTN plan from any objective (with optional `--dry-run` simulation mode).
     - The DAG renders the Root Mission → Decomposed HTN Methods → Durable Leaf Activities with expandable branches, execution duration, tool bindings, and risk scores.
     - Clicking any node opens the **Node Inspector & OITL Control Drawer**, where operators can inspect inputs/outputs, mutate node parameters while paused (`/hitl/modify_node`), or approve/reject high-risk nodes (`/hitl/approve`, `/hitl/reject`).
     - Operators can step through or simulate execution to watch durable activities journal their outputs and transition across states.
  3. **Conversational AJA Chat & Slash Command Flow**:
     - Operators type natural language queries or slash commands with an interactive command palette popup when typing `/`.
     - Supported commands execute immediately against the live runtime state:
       - `/swarm <objective>` & `/goal <objective>`: Decomposes a mission into an HTN Plan Tree and dispatches an Arrow baton.
       - `/todo <title>`, `/doing <id>`, `/done <id>`, `/failed <id>`, `/rmtask <id>`: Mutates tasks in the unified memory store and updates the Priority Engine.
       - `/kanban`: Renders an inline interactive Kanban board inside the chat workspace.
       - `/doctor` & `/status`: Runs environment readiness checks (Native Engine, Memory Manager, Config Validation, Replay Determinism) and prints structured diagnostics.
       - `/models`: Allows switching the active Planner and Worker model assignments.
     - Natural language messages route to the server-side `/api/chat` endpoint powered by `@google/genai` (`gemini-3.8-flash`) with full context of active tasks, batons, and pending approvals.

- **Visual Identity & Theme**:
  - *Aesthetic Direction*: High-craft dark developer & systems orchestration console following the SaaS & Developer Console specification—flat single-elevation surfaces, crisp 1px structural hairlines (`#1E293B`), zero pill-badge clutter, and unboxed typographic metadata separated by `·`.
  - *Color Palette & Mood*:
    - Dominant Neutral Canvas (60%): Deep slate `#0B0F17` (`--background`)
    - Structural Surfaces (30%): Elevated slate `#111827` with `#1E293B` hairline borders and `#94A3B8` muted prose
    - Accent & Semantic Budget (10%): Indigo/Violet `#6366F1` for primary actions and active node selection; semantic text+icon indicators in Emerald `#16A34A` (Completed/Nominal), Amber `#D97706` (Waiting Approval/Warning), and Crimson `#DC2626` (Failed/Blocked)
  - *Typography & Hierarchy*:
    - Display & Navigation: `Plus Jakarta Sans` (SemiBold 600, tight tracking)
    - Body & UI Controls: `DM Sans` (Regular 400 / Medium 500)
    - Telemetry, DAG Trace IDs, Scores & Slash Commands: `JetBrains Mono` with `font-variant-numeric: tabular-nums`
  - *Component Styling & Layout*:
    - Strict 3-zone top bar contract (Brand wordmark on left, 4 single-line view navigation links in center, 1 primary "New Mission" action button on right).
    - Single-elevation panels with no nested card-in-card containers or thick left borders.

- **Interactive Feedback & Motion**:
  - Instant ($\le 150\text{ms}$) compositor-only transitions (`opacity`, `transform`) when expanding HTN DAG branches, filtering priority tasks, or triggering slash-command autocomplete.

---

## 3. Key Product Decisions & Trade-Offs

- **Decision 1: Unified State Synchronization Across Chat, DAG, and Priority Desk**
  - *Chosen Approach*: All slash commands (`/todo`, `/swarm`, `/done`), HTN DAG node transitions, and Priority Engine actions operate on the same canonical server-side in-memory stores and broadcast updates via SSE (`/runtime/stream`) and WebSocket (`/ws/mobile`).
  - *Why*: Ensures that creating a task via `/todo` in AJA Chat or launching a mission via `/swarm` immediately updates the Priority Engine scores, Arrow Baton list, and HTN DAG tree without page reloads.
  - *Alternatives Considered*: Isolated client-side state for chat/DAG—rejected because it would diverge from the REST API endpoints (`/memory/tasks`, `/hitl/*`, `/runtime/*`).
- **Decision 2: Server-Side Gemini Integration with Deterministic Offline Fallback**
  - *Chosen Approach*: Use `@google/genai` (`gemini-3.8-flash`) on the Express server for natural-language chat responses and intelligent HTN plan decomposition, paired with a deterministic rule-based HTN generator fallback when `GEMINI_API_KEY` is not set.
  - *Why*: Guarantees 100% reliability in all environments while providing rich AI-assisted planning and chat when the API key is present.

---

## 4. Technical Architecture & Data Strategy *(Technical Reference)*

- **Architecture & Component Diagram**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       AJA Runtime Operator Console (UI)                      │
│  ┌──────────────────┬──────────────────┬──────────────────┬───────────────┐  │
│  │ Priority Desk &  │ Interactive HTN  │ AJA Chat & Slash │ Workers, OITL │  │
│  │ Task Kanban      │ Plan Tree DAG    │ Command Studio   │ & Event Log   │  │
│  └────────┬─────────┴────────┬─────────┴────────┬─────────┴───────┬───────┘  │
└───────────┼──────────────────┼──────────────────┼─────────────────┼──────────┘
            │ REST / JSON      │ REST / JSON      │ POST /api/chat  │ SSE / WS
            ▼                  ▼                  ▼                 ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│                     Express + TypeScript Runtime Bridge                      │
│  ┌─────────────────────────┬────────────────────────┬─────────────────────┐  │
│  │ Priority & Memory API   │ HTN DAG & OITL Engine  │ Chat & Slash Router │  │
│  │ /priority/engine        │ /htn/plans             │ /api/chat           │  │
│  │ /memory/tasks/*         │ /htn/plans/:id/step    │ /doctor             │  │
│  │ /swarm/run              │ /hitl/approve|reject   │ @google/genai SDK   │  │
│  └────────────┬────────────┴───────────┬────────────┴──────────┬──────────┘  │
│               ▼                        ▼                       ▼             │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │       Canonical In-Memory Runtime Substrate (Tasks, HTN Plans,         │  │
│  │         Arrow Batons, Workers, OITL Approvals, Replay Journal)         │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Data Model & State**:
  - `HTNPlan`: `{ plan_id, objective, dry_run, status, created_at, metrics: { duration_ms, cpu_pct, memory_mb, active_trace }, root: HTNNode }`
  - `HTNNode`: `{ id, title, node_type: 'root' | 'method' | 'activity', tool?: string, command?: string, status: 'PENDING' | 'RUNNING' | 'WAITING_APPROVAL' | 'COMPLETED' | 'FAILED', risk: number, trace_id: string, inputs: Record<string, any>, output?: string, error?: string, children: HTNNode[] }`
  - `ChatEntry`: `{ id, role: 'user' | 'assistant' | 'system', content: string, command_type?: string, payload?: any, created_at: string }`

- **Interactive Component & State Mapping**:
  - **HTN Plan Selector & Generator**: Submitting an objective with optional `Dry Run` toggle calls `POST /htn/plans`, decomposing the mission into a multi-level HTN tree (Root Mission → Methods → Leaf Activities) and linking high-risk leaf nodes (`risk >= 0.7`) to the session's `pending_node` for OITL governance.
  - **HTN Step & Simulation Controls**: Clicking *Step Next Node* or *Simulate Execution* calls `POST /htn/plans/:plan_id/step`, advancing the next runnable leaf node, appending a durable `MissionJournal` event to `runtimeEventsStore`, and pausing automatically on high-risk nodes until operator approval.
  - **Node Inspector & OITL Controls**: Selecting any node in the DAG displays its `trace_id`, `tool`, `inputs`, `risk`, and `output`. If paused in `WAITING_APPROVAL`, the inspector exposes live input editing (`POST /hitl/modify_node`), *Approve & Resume* (`POST /hitl/approve`), and *Reject & Escalate* (`POST /hitl/reject`).
  - **AJA Chat Slash-Command Bar**: Typing `/` opens a keyboard-navigable command suggestion bar; submitting a command calls `POST /api/chat`, executes the command against the unified store, and renders rich inline cards (e.g., Kanban board for `/kanban`, diagnostic check table for `/doctor`, or HTN link for `/swarm`).
