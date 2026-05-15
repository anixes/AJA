import pytest
from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_infrastructure():
    """Mock embedding service and replanner to ensure deterministic, fast testing."""
    # Prevent model loading during initialization
    with patch("agentx.embeddings.service.EmbeddingService._load_model", return_value=None):
        # Patch the single text embedding method
        with patch("agentx.embeddings.service.EmbeddingService.embed") as mock_embed:
            def dummy_embed(text):
                import hashlib
                import numpy as np
                import re
                
                # OPTIMIZATION: Truncate very long text to avoid regex hangs
                if not isinstance(text, str):
                    text = str(text)
                if len(text) > 1000:
                    text = text[:1000]
                    
                # Bag-of-words dummy embedding for deterministic semantic testing
                vec = np.zeros(384, dtype=np.float32)
                words = re.findall(r'\b\w+\b', text.lower())
                if not words:
                    return vec.tolist()
                for w in words:
                    # Use word hash to set an index
                    h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                    vec[h % 384] += 1.0
                
                mag = np.linalg.norm(vec)
                if mag > 0:
                    vec /= mag
                return vec.tolist()
            
            mock_embed.side_effect = dummy_embed
            
            with patch("agentx.embeddings.service.EmbeddingService.embed_batch", create=True) as mock_batch:
                def dummy_embed_batch(texts):
                    return [dummy_embed(t) for t in texts]
                mock_batch.side_effect = dummy_embed_batch
                
                # Mock Replanner.repair_subtree to prevent LLM calls during verification tests
                with patch("agentx.planning.replanner.Replanner.repair_subtree", return_value=False):
                    yield mock_embed
