import time
import pytest
from unittest.mock import patch, MagicMock
from aja.embeddings.service import EmbeddingService
from aja.memory.experience_store import ExperienceStore
from aja.memory.vector import VectorMemory

def test_embedding_service_model_name():
    """Verify that EmbeddingService returns a valid model identifier string."""
    service = EmbeddingService()
    model_name = service.get_model_name()
    assert isinstance(model_name, str)
    assert model_name in ("sentence-transformers/all-MiniLM-L6-v2", "mock-bag-of-words")

def test_experience_store_model_verification():
    """Verify that retrieve_similar skips experiences generated with a mismatched embedding model."""
    store = ExperienceStore()
    
    # Save a mock plan with a specific active model
    with patch.object(EmbeddingService, "get_model_name", return_value="sentence-transformers/all-MiniLM-L6-v2"):
        store.save(
            goal="test model mismatch goal",
            plan=MagicMock(),
            result={"success": True},
            metrics={"latency": 1.2}
        )
    
    assert len(store.store) == 1
    assert store.store[0]["embedding_model"] == "sentence-transformers/all-MiniLM-L6-v2"
    
    # Retrieve with the matching model -> should succeed
    with patch.object(EmbeddingService, "get_model_name", return_value="sentence-transformers/all-MiniLM-L6-v2"):
        results = store.retrieve_similar("test model mismatch goal")
        assert len(results) == 1
        
    # Retrieve with a different model -> should skip and return empty
    with patch.object(EmbeddingService, "get_model_name", return_value="mock-bag-of-words"):
        results = store.retrieve_similar("test model mismatch goal")
        assert len(results) == 0

def test_experience_store_temporal_decay():
    """Verify that temporal decay biases similarity scores toward newer plans."""
    store = ExperienceStore()
    
    # Save experience 1: 100 hours ago
    t_old = time.time() - (100 * 3600.0)
    with patch("time.time", return_value=t_old):
        store.save(
            goal="find the system status",
            plan=MagicMock(),
            result={"success": True},
            metrics={"latency": 0.5}
        )
        
    # Save experience 2: just now
    t_now = time.time()
    with patch("time.time", return_value=t_now):
        store.save(
            goal="find the system status",
            plan=MagicMock(),
            result={"success": True},
            metrics={"latency": 0.5}
        )
        
    assert len(store.store) == 2
    
    # Retrieve similar. Even though the goals are identical (same base cosine similarity),
    # the newer record should decay less and have a higher decayed similarity score.
    with patch("time.time", return_value=time.time()):
        results = store.retrieve_similar("find the system status", top_k=2)
        assert len(results) == 2
        # Experience 2 (timestamp = t_now) should be returned first
        assert results[0]["timestamp"] == t_now
        assert results[1]["timestamp"] == t_old

def test_vector_memory_model_mismatch_warning(capsys):
    """Verify that VectorMemory search prints a warning on model mismatch."""
    vmem = VectorMemory(table_name="test_vector_mismatch_table")
    vmem.clear()
    
    # Add a record with a patched model name
    with patch.object(EmbeddingService, "get_model_name", return_value="old-embedding-model"):
        vmem.add(text="mismatch test text", vector=[0.1]*384, metadata={"key": "val"})
        
    # Search with the current model (which resolves to mock or sentence-transformers)
    with patch.object(EmbeddingService, "get_model_name", return_value="current-embedding-model"):
        results = vmem.search(query_vector=[0.1]*384, limit=1)
        assert len(results) == 1
        
    captured = capsys.readouterr()
    assert "WARNING: Mismatched embedding model" in captured.out
    vmem.clear()

def test_vector_memory_filtering():
    """Verify that VectorMemory search supports metadata-first filtering."""
    vmem = VectorMemory(table_name="test_vector_filtering_table")
    vmem.clear()
    
    # Add two records: one matching condition and one not
    vmem.add(text="match text", vector=[0.1]*384, metadata={"color": "blue"})
    vmem.add(text="other text", vector=[0.1]*384, metadata={"color": "red"})
    
    # Search with a filter on text column
    results = vmem.search(query_vector=[0.1]*384, limit=5, filter_str="text = 'match text'")
    assert len(results) == 1
    assert results[0]["text"] == "match text"
    
    vmem.clear()
