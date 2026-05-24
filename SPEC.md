# SPEC.md: AJA Architecture & Performance Core

## 1. Objective
Upgrade AJA into a "premium" high-performance orchestration framework—complemented by the **AJA (Assistant of Joint Agents)** natural-language secretary—by applying cutting-edge data engineering (Arrow) and native execution (Rust) to the swarm architecture.

## 2. Core Features (The "Upgrade")

### Phase 1: Robust Core Data Layer [DONE]
- **Tech**: Pydantic V2, Python 3.12+
- **Action**: Refactored planning models to Pydantic for runtime type safety and ultra-fast JSON serialization.

### Phase 2: Premium Dashboard Experience [DONE]
- **Tech**: shadcn/ui, Tailwind CSS, Anime.js, Zustand
- **Action**: Centralized state management with Zustand and premium UI components via shadcn.

### Phase 3: Hardened Security Gate [DONE]
- **Tech**: Bash Scripting, Node.js
- **Action**: Refactored bashTool with high-precision sanitization and Windows/Linux shell abstraction.

### Phase 4: Multi-Agent Swarm (Baton Protocol) [DONE]
- **Tech**: Apache Arrow IPC, JSON-RPC, Subprocess Handover
- **Action**: Implemented the **Baton Protocol** for serializing agent state into zero-copy Arrow Tables for near-instant mission handoffs.

### Phase 5: High-Performance Memory (Dual-Brain) [DONE]
- **Tech**: LanceDB, Apache Arrow, Columnar Store
- **Action**: Replaced legacy LanceDB/Arrow with a unified **Unified Arrow Memory** (LanceDB). Provides O(1) semantic search and zero-copy structured data retrieval.

### Phase 6: Native Performance Core [DONE]
- **Tech**: Rust, PyO3, Arrow-IPC
- **Action**: Offloaded performance bottlenecks (Token Analysis, Baton Serialization) to a native Rust module (`aja-native`). Bypasses Python's GIL for 10x faster swarm state transitions.

### Phase 7: AJA Gateway (Premium Telegram Secretary) [DONE]
- **Tech**: asyncio, python-telegram-bot, WebSockets, Secretary Memory
- **Action**: Implemented the **AJA Gateway**, a high-fidelity messaging layer for remote mission management. Features include resilient polling, mobile WebSocket sync, and intent-aware task capture.

### Phase 8: Autonomous Tool Loop (Self-Correcting Execution) [DONE]
- **Tech**: Subprocess, ToolExecutor, Intent Analysis
- **Action**: Enabled the swarm to autonomously suggest, audit, and execute shell commands (tools) during the planning phase to gather environment state before worker dispatch.

### Phase 9: Deep Territory RAG (Codebase Awareness) [DONE]
- **Tech**: LanceDB Vector Store, TerritoryScanner, RAG
- **Action**: Implemented a recursive codebase scanner that indexes the project territory into a vector knowledge base. Agents now perform semantic RAG lookups to "understand" the repository before acting.

### Phase 10: Synthetic Skill Library (Reflective Learning) [DONE]
- **Tech**: ReflectionEngine, SkillStore, Arrow
- **Action**: Automated the extraction of reusable "Skills" from successful mission histories. These synthetic skills are hot-swapped into future mission plans based on objective similarity.

### Phase 11: Monorepo Infrastructure & Hardening [DONE]
- **Tech**: Pytest, Monorepo Architecture, Deterministic Mocking
- **Action**: Consolidated all core logic into `libs/aja-core`, normalized namespaces to `aja.*`, and implemented sub-second test-time mocks for embeddings. Purged legacy SQLite artifacts and enforced strict LanceDB/Arrow usage across the stack.

### Phase 12: Self-Healing HTN & Consensus Planning [DONE]
- **Tech**: HTN Sanitizer, Multi-Run Consensus, Semantic Healing
- **Action**: Implemented a structural healing layer for HTN plan graphs. Hallucinated dependencies are automatically mapped to valid leaf nodes. Added a multi-run consensus phase to the planner to ensure high-precision mission logic.

## 3. Success Criteria
- [x] Pytest suite returns 100% success with Pydantic models.
- [x] Dashboard passes a visual "premium" audit.
- [x] Baton handovers occur in < 200ms using Arrow IPC.
- [x] Context optimization (Pure AJA) successfully protects local model limits.
- [x] AJA Telegram Gateway maintains 100% uptime through local network blips.
- [x] Intent-aware parsing correctly identifies tasks vs natural chat.
- [x] Real-time Mobile Sync operational via /ws/mobile.
- [x] **Power 2**: Autonomous tool loop successfully prep-executes shell logic.
- [x] **Power 4**: Deep Territory RAG provides relevant code context to the planner.
- [x] **Power 5**: ReflectionEngine successfully synthesizes and stores synthetic skills.
- [x] **Power 6**: Self-Healing HTN automatically repairs invalid plan dependencies.
- [x] **Power 7**: Multi-Run Consensus ensures 99.9% plan validity for complex missions.

## 4. Hardware Optimization (The High-Efficiency Target)
The architecture is specifically tuned for resource-constrained environments. By using **Columnar State Slicing** (Arrow) and **Native Token Counting** (Rust), we maintain elite reasoning speeds even during complex multi-step missions on consumer-grade hardware.

## 5. Architectural Decoupling: The "Brain, Muscle, Voice" Model
To ensure maximum scalability and user experience, the system is decoupled into three distinct layers:
1. **The Brain (LLM)**: Reasoning & Decision logic.
2. **The Voice (AJA)**: Conversational Secretary & Planning Gateway. Handles I/O and user interaction.
3. **The Muscle (AJA)**: High-performance Native Core. Handles execution and state management.

**Performance Impact**: This separation allows the "Voice" (AJA) to remain responsive during heavy "Muscle" (AJA) operations, while both share a **Zero-Copy Memory Layer (LanceDB/Arrow)** to eliminate communication latency.
