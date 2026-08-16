# Cognitive Architecture: Frontier Autonomous Agent OS

AJA incorporates a state-of-the-art cognitive runtime synthesizing peer-reviewed research and enterprise standards in cognitive architectures, operating systems for language agents, executable action spaces, test-time compute, and temporal knowledge graphs.

---

## 1. Research & Industry Foundations

AJA's cognitive layer is built on five core pillars:

1. **CoALA 2.0 & Bi-Temporal Knowledge Graphs** (*Zep/Graphiti / Letta standard*):
   - Implements a bi-temporal relational graph separating **Valid Time** (when a fact was true in the environment) from **Transaction Time** (when the system recorded it). Contradictions are resolved non-destructively via cascade invalidation (`valid_to = now`), preserving full historical provenance.
2. **System-2 Test-Time Compute (TTC) & State Tree Backtracking**:
   - For high-stakes, ambiguous, or multi-step missions, AJA generates $N$ candidate execution branches, scores rollouts based on predicted utility, and maintains an in-memory state tree with checkpoints that automatically rewinds to the parent node upon step failures.
3. **Autonomous Procedural Self-Evolution** (*agentskills.io standard*):
   - Automatically distills winning multi-turn trajectories into verified, parameterized skills under `~/.aja/skills/<name>/` (`SKILL.md` + `run.py`) with AST dry-run validation gates.
4. **Universal Stateless MCP Dynamic Mesh**:
   - Native client conforming to the stateless Model Context Protocol (MCP) standard with runtime `tools/list` dynamic discovery and `maxTokenBudget` context protection.
5. **AIOS & Replay-Authoritative Execution Kernel**:
   - Host operating system kernel abstraction providing priority scheduling (`URGENT`, `NORMAL`, `BACKGROUND`), coroutine-isolated `WorkspaceContext`, and zero-copy Apache Arrow memory batons (`aja_native`).

---

## 2. Memory Substrates (The CoALA 2.0 Bi-Temporal Stack)

AJA avoids using a single database as a catch-all memory store. Instead, each memory type is assigned to its optimal storage substrate:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AJA FRONTIER COGNITIVE MEMORY STACK                      │
├───────────────────┬─────────────────────────┬───────────────────────────────┤
│ Memory Substrate  │ Storage Medium          │ Primary Function              │
├───────────────────┼─────────────────────────┼───────────────────────────────┤
│ Working Memory    │ Coroutine RAM / Context │ Sub-goals, hypotheses,        │
│                   │ (`WorkingMemory`)       │ observation scratchpad        │
├───────────────────┼─────────────────────────┼───────────────────────────────┤
│ Bi-Temporal Graph │ SQLite + FTS5           │ Entity-relationship graph     │
│ Memory            │ (`temporal_graph.db`)   │ with valid_time/valid_to      │
├───────────────────┼─────────────────────────┼───────────────────────────────┤
│ Episodic Memory   │ LanceDB Vector Tables   │ Trajectory post-mortems,      │
│                   │ (`aja_episodic`)        │ vector similarity recall      │
├───────────────────┼─────────────────────────┼───────────────────────────────┤
│ Procedural Memory │ Filesystem Tree         │ Executable Python/Bash skills │
│                   │ (`~/.aja/skills/`)      │ (agentskills.io standard)     │
└───────────────────┴─────────────────────────┴───────────────────────────────┘
```

### A. Working Memory (`WorkingMemory`)
- Lives in volatile, coroutine-isolated memory (`ContextVar`).
- Tracks the active mission goal, sub-goal stack, scratchpad thoughts, and immediate tool observations.
- Uses sliding window compaction to avoid context window degradation during long-running tasks.

### B. Bi-Temporal Entity Graph (`BiTemporalEntityGraph`)
- Stored deterministically in `~/.aja/state/temporal_graph.db` with SQLite WAL mode and FTS5 full-text indexing.
- Tracks dual timelines (`valid_from` / `valid_to` vs `recorded_at`).
- Supports point-in-time historical queries (`get_entity_history`) and non-destructive fact invalidation, eliminating temporal vector collisions.

### C. Episodic Memory (`EpisodeReflection`)
- Stored in LanceDB vector tables (`aja_episodic_reflections`).
- Encapsulates past task trajectories, key lessons learned, error post-mortems, and successful strategies.
- Queried via cosine similarity to recall relevant past experiences before generating plans.

### D. Procedural Memory (`ProceduralSkill` & `SkillCompiler`)
- Stored in standard directory trees (`~/.aja/skills/<skill_name>/`).
- Synthesized automatically from winning missions by the `SkillCompiler`.
- Adheres to the open **agentskills.io** format (`SKILL.md` frontmatter + markdown documentation + executable Python/Bash scripts).

---

## 3. System-2 Test-Time Compute (TTC) & Dynamic Backtracking

AJA transitions between **System-1 Fast Reflex** (direct tool execution / telemetry lookups) and **System-2 Test-Time Compute (TTC)**:

```mermaid
graph TD
    Goal[Mission Objective] --> Decision{Risk / Complexity Evaluation}
    
    Decision -->|Low Risk / Telemetry| Reflex[System-1 Fast Reflex Loop]
    Decision -->|High Risk / Refactor| TTC[System-2 TTC Planner]
    
    subgraph TTC Engine
        TTC --> Gen[Generate N Candidate Branches]
        Gen --> Score[Score Utility = P_success * (1 - 0.5 * Risk)]
        Score --> Tree[StateTree Checkpoint Execution]
        Tree --> Step{Step Execution}
        Step -->|Success| NextStep[Next Step / Complete]
        Step -->|Failure / Crash| Backtrack[Auto-Backtrack to Parent Checkpoint]
        Backtrack --> NextBranch[Try Next Highest Scoring Branch]
    end
    
    Reflex --> Outcome[Mission Outcome]
    NextStep --> Outcome
    Outcome --> Compiler[SkillCompiler Auto-Distillation]
    Compiler --> Skills[~/.aja/skills/]
```

---

## 4. Universal Stateless MCP Dynamic Mesh

AJA provides first-class support for the Model Context Protocol (MCP):
- **Stateless by Default**: Ephemeral execution without session-locking overhead.
- **Dynamic Discovery**: Auto-probes server capabilities at runtime via `tools/list`.
- **Context Budget Guard**: Enforces `maxTokenBudget` parameter on tool outputs to protect LLM context windows.
- **Transports**: STDIO for local subprocesses and Streamable HTTP for remote cloud microservices.

---

## 5. Tiered System Prompt & SOUL.md Layering

AJA's system prompt is synthesized dynamically across 8 distinct architectural layers:

1. **Stable Tier (Identity & Voice)**: Loaded from `~/.aja/SOUL.md` or `DEFAULT_SOUL` (direct, developer-fluent, non-sycophantic, non-destructive bias).
2. **Specialist Role**: `SysAdminSpecialist`, `WebResearchSpecialist`, or `CodeEngineerSpecialist`.
3. **Unified CodeAct Action Space**: Python and Bash executable block guidelines.
4. **Workspace Context & Sandboxing**: Active workspace ID, root path, and boundary enforcement.
5. **Project Guidelines**: Auto-ingested from `AGENTS.md`, `CLAUDE.md`, or `.cursorrules`.
6. **Bi-Temporal Knowledge Graph Context**: Active environment facts and entity relations.
7. **Procedural Skills Registry**: Available custom skills in `~/.aja/skills/`.
8. **Episodic Recall**: Lessons learned from similar past missions.
