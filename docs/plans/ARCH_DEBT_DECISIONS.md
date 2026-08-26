# Architecture Debt — Decision-Ready Analysis

Research date: 2026-08-26. For owner review; each item has a decision gate.

## Item 1 — Episodic memory keyword-vs-vector

FINDING: ledger was HALF-STALE. Chat recall already vector-based
(gateway/recall.py hybrid = dense+FTS5+RRF over mission_semantic table).
What remains weak:
- cognitive/memory_manager.py:174-224 recall_episodes = literal substring
  token counting over JSON episode files. Consumer: cognitive/orchestrator
  :84-94 injects lessons into prompts.
- experience_store.py holds embedded goals in RAM ONLY (self.store=[]) —
  all plan-biasing experience evaporates on restart. Arguably worse debt.

INFRASTRUCTURE EXISTS: EmbeddingService singleton, VectorMemory(table_name=
...) generic 384-dim add/search, precedent tables mission_semantic +
aja_territory_knowledge, reindex CLI support.

RECOMMENDATION: Option 1 — reuse VectorMemory(table_name="aja_episodes"):
write summary row in save_episode, replace token scoring with vm.search;
keep JSON files as human-readable source of truth; backfill script ~30
lines mirroring _reindex_vector_memory_tables; fallback to keyword if
embedder unavailable. Effort 0.5-1 day. Optional +1 day: persist
ExperienceStore to its own VectorMemory table.

DECISION NEEDED: fund the fix (+optional experience persistence) or accept
keyword recall and close the item?

## Item 2 — Overlapping task stores

INVENTORY: aja_tasks hosts TWO entity types — personal tasks AND scheduler
jobs (owner="scheduler" fork inside secretary.create_task/update_task
L470/L600-641; create_task for scheduler rows is a journal-emitting NO-OP
that returns get_task() of a possibly-nonexistent row). Missions separate
in aja_missions + mission_journal.jsonl. Three in-process RAM schedulers
(not persistence). Journals are the event-source of record.

SHARPEST BUG-RISK: the owner=="scheduler" fork — silent-write semantics +
every consumer (briefing, priority engine, REST) must remember to filter
by owner or cron jobs appear as personal tasks.

RECOMMENDATION: NOT full merge. Surgical split: give scheduler jobs their
own aja_scheduled_jobs table, delete the fork (~80 most bug-prone lines in
secretary.py), write an ADR (tasks ≠ jobs ≠ missions; journals are truth).
Leave missions separate — genuinely different lifecycle.
Effort: 2-3 days incl. tests + briefing/API adjustments; ADR half day.

DECISION NEEDED: approve behavioral change (anything reading aja_tasks for
scheduler rows must migrate) OR fund ADR now and defer split until incident?

## Item 3 — Orphaned agent_memory table

CONFIRMED ORPHAN: default constructor arg of VectorMemory (vector.py:20);
no production caller uses the default (gateway uses mission_semantic);
reindex CLI iterates it but tolerates absence; one test uses it as a string.
Zero writers, zero readers. On-disk data = dead pre-mission_semantic era.

RECOMMENDATION: Drop. Remove from reindex tuple; make VectorMemory
table_name REQUIRED (prevents recurrence better than re-pointing default);
one-off drop_table; update test string. Nothing functional breaks; verify
no external tooling queries it first. Effort ~1 hour.

## Suggested order
1. agent_memory drop (1h, trivial)
2. episodic vector index (+experience persistence) (1-2 days)
3. jobs/tasks split + ADR (needs explicit owner approval — behavioral change)
