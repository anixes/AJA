"""
AJA CLI Command: reindex-embeddings
===================================
Rebuilds persisted vector stores with the currently configured embedding
model. Run this after changing SwarmSettings.embedding_model — vectors from
different models live in incompatible spaces and are filtered out at search
time until reindexed.
"""

import json
import logging

logger = logging.getLogger(__name__)

_REINDEX_BATCH = 64


def _reindex_vector_memory_tables(db) -> dict[str, int]:
    """Re-embeds text rows in VectorMemory-style tables (vector/text/metadata/timestamp)."""
    from aja.embeddings.service import get_embedding_service
    from aja.memory.manager import list_tables_defensive
    from aja.memory.vector import VectorMemory

    service = get_embedding_service()
    counts: dict[str, int] = {}
    existing = list_tables_defensive(db)
    for table_name in ("mission_semantic", "aja_episodes"):
        if table_name not in existing:
            counts[table_name] = -1  # not present, skipped
            continue
        table = db.open_table(table_name)
        rows = table.to_arrow().to_pylist()
        if not rows:
            counts[table_name] = 0
            continue

        texts = [r["text"] or "" for r in rows]
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _REINDEX_BATCH):
            vectors.extend(service.embed_many(texts[start : start + _REINDEX_BATCH]))

        memory = VectorMemory(table_name=table_name)
        memory.clear()
        target = db.open_table(table_name)
        payload = []
        for row, vec in zip(rows, vectors, strict=True):
            metadata = {}
            try:
                metadata = json.loads(row.get("metadata") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            metadata["embedding_model"] = service.get_model_name()
            payload.append(
                {
                    "vector": vec,
                    "text": row["text"],
                    "metadata": json.dumps(metadata),
                    "timestamp": row.get("timestamp") or 0.0,
                }
            )
        for start in range(0, len(payload), _REINDEX_BATCH):
            target.add(payload[start : start + _REINDEX_BATCH])
        counts[table_name] = len(payload)
    return counts


def _reindex_territory_knowledge() -> dict[str, int]:
    """Re-embeds the secretary-owned RAG table (id/path/content/metadata_json/updated_at/vector)."""
    from aja.embeddings.service import get_embedding_service
    from aja.memory.secretary import (
        TERRITORY_KNOWLEDGE_SCHEMA,
        get_aja_memory,
    )

    memory = get_aja_memory()
    db = memory.db
    from aja.memory.manager import list_tables_defensive

    if "aja_territory_knowledge" not in list_tables_defensive(db):
        return {"aja_territory_knowledge": -1}

    table = db.open_table("aja_territory_knowledge")
    rows = table.to_arrow().to_pylist()
    if not rows:
        return {"aja_territory_knowledge": 0}

    service = get_embedding_service()
    texts = [r.get("content") or "" for r in rows]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), _REINDEX_BATCH):
        vectors.extend(service.embed_many(texts[start : start + _REINDEX_BATCH]))

    db.drop_table("aja_territory_knowledge")
    db.create_table("aja_territory_knowledge", schema=TERRITORY_KNOWLEDGE_SCHEMA)
    target = db.open_table("aja_territory_knowledge")
    payload = []
    for row, vec in zip(rows, vectors, strict=True):
        metadata = {}
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (TypeError, ValueError):
            metadata = {}
        metadata["embedding_model"] = service.get_model_name()
        payload.append(
            {
                "id": row.get("id"),
                "path": row.get("path"),
                "content": row.get("content"),
                "metadata_json": json.dumps(metadata),
                "updated_at": row.get("updated_at"),
                "vector": vec,
            }
        )
    for start in range(0, len(payload), _REINDEX_BATCH):
        target.add(payload[start : start + _REINDEX_BATCH])
    return {"aja_territory_knowledge": len(payload)}


def cmd_reindex_embeddings():
    """Rebuilds all persisted vector stores with the current embedding model."""
    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    from aja.embeddings.service import get_active_model, get_embedding_service

    console.print(
        Panel(
            f"Reindexing vector stores with model: [bold cyan]{get_active_model()}[/bold cyan]",
            title="AJA Embedding Reindex",
            border_style="cyan",
        )
    )

    from aja.memory.manager import get_memory_manager

    db = get_memory_manager().db
    try:
        counts = _reindex_vector_memory_tables(db)
    except Exception as e:
        logger.exception("VectorMemory reindex failed")
        console.print(f"[red]VectorMemory reindex failed:[/] {type(e).__name__}: {e}")
        counts = {}
    try:
        counts.update(_reindex_territory_knowledge())
    except Exception as e:
        logger.exception("Territory knowledge reindex failed")
        console.print(f"[red]Territory knowledge reindex failed:[/] {type(e).__name__}: {e}")

    any_done = False
    for table_name, count in counts.items():
        if count < 0:
            console.print(f"  [dim]- {table_name}: not present, skipped[/dim]")
        else:
            any_done = True
            console.print(f"  [green]✔ {table_name}: {count} rows re-embedded[/green]")

    if not any_done:
        console.print("[yellow]Nothing to reindex — vector stores are empty or absent.[/yellow]")
    else:
        console.print(
            "[bold cyan]Engine:[/] Reindex complete. New searches now use "
            f"'{get_embedding_service().get_model_name()}' vectors exclusively."
        )
