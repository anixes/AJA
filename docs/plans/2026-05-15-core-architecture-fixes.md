# Core Architecture Refinement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Standardize embedding dimensions, enable real semantic search in the Skill Store, and refine the Reflection Engine's synthesis logic to resolve critical architectural technical debt.

**Architecture:** We will standardize on a 384-dimensional vector space for local-first performance (MiniLM), implement native LanceDB vector retrieval in `SkillStore`, and harden the Reflection Engine with schema-based JSON extraction.

**Tech Stack:** Python 3.12, LanceDB, PyArrow, Sentence-Transformers.

---

### Task 1: Standardize Embedding Dimension to 384

**Files:**
- Modify: `packages/agentx-core/agentx/skills/skill_store.py:51`
- Modify: `packages/agentx-core/agentx/memory/secretary.py` (Verify any hardcoded 1536 dims)

**Step 1: Update SkillStore schema**
Change `pa.list_(pa.float32(), 1536)` to `pa.list_(pa.float32(), 384)`.

**Step 2: Update save_skill placeholder vector**
Update `vector = [0.0] * 1536` to `vector = [0.0] * 384`.

**Step 3: Run validation**
Run: `python -m agentx.skills.skill_store` (Add a simple test main if not present)
Expected: Table creates successfully with 384D vector column.

### Task 2: Implement Real Vector Search in SkillStore

**Files:**
- Modify: `packages/agentx-core/agentx/skills/skill_store.py:117-132`

**Step 1: Import embedding model utility**
Add `from agentx.memory.territory import _get_embedding` or similar utility access.

**Step 2: Update search_skills to use vector search**
```python
def search_skills(self, query_text: str, limit: int = 10) -> List[Dict[str, Any]]:
    table = self.db.open_table("aja_skills")
    from agentx.memory.territory import TerritoryScanner
    scanner = TerritoryScanner()
    query_vector = scanner._get_embedding(query_text)
    return table.search(query_vector).limit(limit).to_list()
```

**Step 3: Test semantic retrieval**
Verify that a query for "file operations" returns the "disk-cleanup" skill even without keyword match.

### Task 3: Harden Reflection Engine Synthesis

**Files:**
- Modify: `packages/agentx-core/agentx/autonomy/reflection.py:47-64`

**Step 1: Add few-shot examples to the prompt**
Update the prompt to include a successful skill extraction example to guide the LLM.

**Step 2: Implement rigid JSON extraction**
Refine the `response_str` cleaning logic to handle more varied markdown formats.

### Task 4: Improve RAG Chunking Strategy

**Files:**
- Modify: `packages/agentx-core/agentx/memory/territory.py:131-133`

**Step 1: Implement line-aware chunking**
Replace character slicing with a strategy that respects line breaks to preserve code context.

```python
def _chunk_content(self, content: str, chunk_size: int = 1000) -> List[str]:
    lines = content.splitlines()
    chunks = []
    current_chunk = []
    current_size = 0
    for line in lines:
        if current_size + len(line) > chunk_size and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_size = 0
        current_chunk.append(line)
        current_size += len(line)
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks
```
