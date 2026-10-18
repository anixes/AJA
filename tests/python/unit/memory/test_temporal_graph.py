"""
=============================================================================
AJA Cognitive Architecture: Bi-Temporal Knowledge Graph Unit Tests
=============================================================================
"""

import time
import pytest
from pathlib import Path

from aja.cognitive.temporal_graph import BiTemporalEntityGraph


def test_bitemporal_upsert_and_invalidation(tmp_path):
    """Verify that updating an entity preserves history with valid_to timestamps."""
    db_path = tmp_path / "test_graph.db"
    graph = BiTemporalEntityGraph(db_path=db_path)

    # 1. Insert initial entity: PostgreSQL on port 5432
    t0 = time.time() - 10.0
    e1 = graph.upsert_entity("service", "postgres", {"port": 5432, "version": "15.4"}, valid_from=t0)
    assert e1.name == "postgres"
    assert e1.is_active is True

    # 2. Update entity: PostgreSQL upgraded to port 5433 at t1
    t1 = time.time()
    e2 = graph.upsert_entity("service", "postgres", {"port": 5433, "version": "16.1"}, valid_from=t1)
    assert e2.entity_id != e1.entity_id
    assert e2.properties["port"] == 5433

    # 3. Active query must return latest state
    active = graph.get_active_entity("service", "postgres")
    assert active is not None
    assert active.entity_id == e2.entity_id
    assert active.properties["port"] == 5433

    # 4. History query must return both revisions
    history = graph.get_entity_history("service", "postgres")
    assert len(history) == 2
    assert history[0].properties["port"] == 5432
    assert history[0].valid_to is not None  # Old entity is invalidated
    assert history[1].properties["port"] == 5433
    assert history[1].valid_to is None      # New entity is active


def test_bitemporal_relationships_and_queries(tmp_path):
    """Verify directed relational graph linking between entities."""
    db_path = tmp_path / "test_graph.db"
    graph = BiTemporalEntityGraph(db_path=db_path)

    srv = graph.upsert_entity("service", "api_backend", {"framework": "fastapi"})
    db = graph.upsert_entity("database", "main_db", {"engine": "postgres"})

    # Link: api_backend DEPENDS_ON main_db
    rel = graph.add_relation(srv.entity_id, db.entity_id, "DEPENDS_ON", {"pool_size": 20})
    assert rel.relation_type == "DEPENDS_ON"

    # Query relations for api_backend
    relations = graph.get_relations(srv.entity_id)
    assert len(relations) == 1
    assert relations[0].target_id == db.entity_id


def test_full_text_search_and_context_summary(tmp_path):
    """Verify FTS5 full-text search and markdown summary generation."""
    db_path = tmp_path / "test_graph.db"
    graph = BiTemporalEntityGraph(db_path=db_path)

    graph.upsert_entity("docker_container", "redis_cache", {"image": "redis:7-alpine", "status": "running"})
    graph.upsert_entity("config_file", "nginx_conf", {"path": "/etc/nginx/nginx.conf", "ssl": True})

    # FTS search
    results = graph.search_entities("alpine")
    assert len(results) >= 1
    assert results[0].name == "redis_cache"

    # Context summary
    summary = graph.get_context_summary()
    assert "Active Environment Knowledge Graph" in summary
    assert "redis_cache" in summary
    assert "nginx_conf" in summary
