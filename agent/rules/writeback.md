---
name: writeback
description: Guidelines on how agents can write back execution logs and feedback.
---
# Writeback Protocol

When updating task completion status:
1. Ensure all task transitions and worker outcomes are committed to the immutable append-only `.jsonl` timeline log.
2. Rebuild Fast-path LanceDB projections using `python -m aja rebuild-projections` to ensure fast query sync.
3. Save local JSON results and patch diffs in the session execution folder (`.aja/executions/<session_id>/`).
