# AJA Structure & Module Map

This document outlines the codebase structure and logical module responsibilities across the AJA monorepo.

---

## 1. Core Architecture Map (`libs/aja-core/aja/`)

```
libs/aja-core/aja/
├── cognitive/                 # CoALA Tripartite Memory, CodeAct & Magentic-One Orchestrator
│   ├── memory_models.py       # Domain models: Working, Semantic, Episodic, Procedural
│   ├── memory_manager.py      # CoALA Memory Manager (LanceDB vectors, skills, facts)
│   ├── codeact.py             # ICML 2024 CodeAct Engine (Python/Shell execution traps)
│   ├── specialists.py         # Magentic-One Roles (SysAdmin, WebResearcher, CodeEngineer)
│   └── orchestrator.py        # Cognitive Orchestrator loop & routing
├── workspace/                 # Multi-Workspace Management & Isolation
│   ├── context.py             # Coroutine-isolated WorkspaceContext via ContextVar
│   └── manager.py             # WorkspaceRegistry (~/.aja/workspaces.json)
├── kernel/                    # Operating System Kernel & Scheduling
│   └── scheduler.py           # Priority Async Mission Queue (URGENT / NORMAL / BACKGROUND)
├── orchestration/             # Swarm Execution & Tool Infrastructure
│   ├── activity_rt.py         # Durable Activity Runtime & Replay Interceptor
│   ├── scheduler.py           # Parallel Activity Scheduler
│   └── tools/                 # Native Tool Registries
│       ├── native.py          # NativeToolRegistry
│       ├── executor.py        # Sandboxed ToolExecutor
│       ├── sys_tools.py       # Host & Docker inspection tools
│       └── web_tools.py       # DuckDuckGo/Tavily search & URL scraper
├── security/                  # Security Governance & Sandboxing
│   ├── command_guard.py       # Ambient vs Workspace Boundary Enforcement
│   ├── permissions.py         # PermissionEngine & Scoped Authorization
│   └── stripper.py            # Token & command normalizer
├── memory/                    # Persistent Storage Substrates
│   └── workspace_pool.py      # LRU-cached LanceDB Memory Manager Pool
├── cli/                       # Command-Line Interfaces
│   ├── commands/              # CLI Subcommands (ws, run, chat, doctor, etc.)
│   └── main.py                # AJA Root Entrypoint
└── gateway/                   # Remote Messaging Gateways
    └── orchestrator.py        # Telegram Remote Control & Multi-Workspace Gateway
```

---

## 2. Directory Reorganization & Package Layout

| Path | Purpose |
| :--- | :--- |
| `libs/aja-core/` | Core Python runtime, cognitive engine, kernel, and security policies. |
| `libs/aja-native/` | Rust PyO3 native extensions for zero-copy Arrow state serialization. |
| `tests/python/unit/` | Fast, deterministic unit tests (CoALA memory, CodeAct, security). |
| `tests/python/integration/` | End-to-end integration tests (CLI, durability, workspace sandboxing). |
| `scripts/vps/` | VPS deployment, systemd daemons, logrotate, and control scripts. |
| `docs/` | Architectural specifications, cognitive models, and operator guides. |

