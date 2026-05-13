# Architectural Knowledge: Agent Monorepo Modernization

## Overview
The Agent project has been refactored from a "scattered" root-level directory structure into a modern **Apps/Packages Monorepo**. This structure enforces a clean separation between core logic, peripheral applications, and runtime state.

## 🕸️ Semantic Knowledge Graph
The codebase is indexed via **Graphify**, providing a navigable map of semantic relationships and architectural hubs.

**Latest Graph Report**: [GRAPH_REPORT.md](../graphify-out/GRAPH_REPORT.md)

### Key Architectural Hubs
Based on the latest graph analysis, the system is clustered into the following functional domains:
*   **Planning & HTN System**: Core HTN implementation, DAG validation, and serializability.
*   **Decision Engine**: LLM-assisted routing and meta-evaluation logic.
*   **API & Security**: The bridge between the Python core and TypeScript apps, including command stripping and sandboxing.
*   **Memory & Intelligence**: Experience stores, failure categorization, and embedding-based retrieval.
*   **Skill Execution**: The idempotent execution loop and runtime state management.

---

## Core Structure

### 1. Packages (`/packages`)
- **`agent-core`**: The authoritative source of truth for all Python logic.
  - **`agent/`**: Main package namespace.
    - `agents/`: Autonomous agent logic and specialized worker definitions.
    - `api/`: The FastAPI bridge (formerly `api_bridge.py`) and network interfaces.
    - `orchestration/`: Swarm engine logic and the Unified AI Gateway.
    - `memory/`: Secretary memory (LanceDB/Arrow) and long-term context management.
    - `security/`: Command stripping, safety auditing, and risk classification.
    - `utils/`: Consolidates supplemental utility scripts (graph watchers, health checks, etc.).
    - `main.py`: The unified Python entry point for the swarm toolkit.

### 2. Applications (`/apps`)
- **`cli-ts`**: A standalone TypeScript/Node.js application providing the Ink-based CLI experience and simulation layers.
- **`dashboard`**: A high-fidelity React/Vite web application for executive supervision.

### 3. Testing (`/tests`)
- **`python/`**: Pytest-based suite for validating core engine logic.
- **`typescript/`**: Vitest/Jest suite for validating CLI and dashboard components.

### 4. Persistence (`/.agent`)
- All runtime state, LanceDB/Arrow databases, audit logs, and temporary batons are now localized here. This ensures the project root remains clean and the environment is highly portable.

## Standardized Entry Points

### Python (`agent.bat`)
The `agent.bat` at the root now automates the setup of `PYTHONPATH` to include `packages/agent-core`. All core logic is executed via:
```powershell
python -m agent [command]
```

### Node.js (`package.json`)
The root `package.json` utilizes **npm workspaces**.
- `npm install` installs dependencies across the entire monorepo.
- `npm run dev` and `npm run dev:cli` route execution to the respective applications in `apps/`.

## Key Architectural Decisions
1. **Implicit Namespace Packages**: The migration from `scripts/*.py` to `agent.*` modules resolved numerous import resolution issues caused by relative path dependencies.
2. **Dynamic Root Detection**: All persistence paths are now resolved relative to the detected `PROJECT_ROOT`, removing the "absolute path" fragility previously found in `GoalEngine` and `PolicyStore`.
3. **Workspaces over Submodules**: Chose npm workspaces for the frontend/CLI to ensure a unified versioning and dependency lifecycle.

## Future Maintenance
- **New Agents**: Should be added to `packages/agent-core/agent/agents/`.
- **New UI Components**: Should be added to `apps/dashboard/src/` or `apps/cli-ts/src/ui/`.
- **State Migration**: Any new persistence files MUST be stored within the `.agent/` directory to maintain portability.
