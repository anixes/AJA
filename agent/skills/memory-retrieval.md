---
name: memory-retrieval
description: How to query AJA's memory stack — LanceDB tables, semantic search, experience store, and territory knowledge.
---
# Memory Retrieval

AJA has a layered memory stack. Use the right layer for the right query.

## 1. Structured task / mission records (`AJAMemory`)

```python
from aja.memory.secretary import get_aja_memory

mem = get_aja_memory()

# Look up a task by ID
task = mem.get_task("TASK-ID")

# List all pending tasks
pending = mem.list_tasks(status="pending")

# Look up a mission
mission = mem.get_mission("M-XXXXXX")
```

## 2. Semantic similarity search (`ExperienceStore`)

Use when you need to find past tool executions or outcomes similar to a natural-language query.

```python
from aja.memory.experience_store import experience_store

results = experience_store.search("database migration failed", limit=5)
for r in results:
    print(r["objective"], r["outcome"])
```

## 3. Territory knowledge (RAG chunks)

Use when you need to find code or documentation indexed from the workspace.

```python
from aja.memory.secretary import get_aja_memory

mem = get_aja_memory()
chunks = mem.search_territory("Arrow IPC baton serialization", limit=10)
for chunk in chunks:
    print(chunk["path"], chunk["content"][:200])
```

## 4. Failure memory

Use to avoid repeating known failure patterns.

```python
from aja.memory.failure_memory import failure_memory

known = failure_memory.search("subprocess timeout", limit=3)
```

## Rules
- Always prefer `get_aja_memory()` singleton — never instantiate `AJAMemory` directly.
- Semantic search requires the embedding model to be loaded; expect ~1 s on first call.
- `aja_territory_knowledge` vectors are rebuilt with `python -m aja rebuild-projections`.
