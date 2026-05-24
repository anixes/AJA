import os
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from aja.config import PROJECT_ROOT
from aja.memory.secretary import get_aja_memory

logger = logging.getLogger(__name__)

_embedding_model = None


def get_embedding_model():
    """Lazy-loads the semantic embedding model."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from sentence_transformers import SentenceTransformer

            # Use a lightweight, high-performance model (384 dimensions)
            _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. Falling back to placeholder embeddings."
            )
            return None
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            return None
    return _embedding_model


class TerritoryScanner:
    """
    Scans defined territories and indexes them into AJA Memory for RAG.
    """

    SUPPORTED_EXTENSIONS = {
        ".py",
        ".ts",
        ".js",
        ".json",
        ".md",
        ".txt",
        ".sql",
        ".yaml",
        ".yml",
    }
    EXCLUDE_DIRS = {
        "node_modules",
        ".git",
        "__pycache__",
        "dist",
        "build",
        ".next",
        ".aja",
    }

    def __init__(self, territories: List[Dict[str, Any]] = None):
        if territories is None:
            # Load from aja.json
            config_path = PROJECT_ROOT / "aja.json"
            if config_path.exists():
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                    self.territories = cfg.get("territories", [])
            else:
                self.territories = []
        else:
            self.territories = territories

    async def scan_all(self):
        """Walks through all territories and indexes files."""
        logger.info(
            f"AJA Memory: Starting territory scan for {len(self.territories)} locations."
        )
        mem = get_aja_memory()

        for territory in self.territories:
            rel_path = territory.get("path")
            if not rel_path:
                continue

            abs_path = PROJECT_ROOT / rel_path
            if not abs_path.exists():
                logger.warning(f"Territory path does not exist: {abs_path}")
                continue

            # Idempotency: Clear existing knowledge for this territory path prefix
            mem.clear_territory_knowledge(path_prefix=rel_path)

            await self._scan_directory(abs_path)

        logger.info("AJA Memory: Territory scan complete.")

    async def _scan_directory(self, root: Path):
        mem = get_aja_memory()
        for path in root.rglob("*"):
            if any(part in self.EXCLUDE_DIRS for part in path.parts):
                continue

            if path.is_file() and path.suffix in self.SUPPORTED_EXTENSIONS:
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    if not content.strip():
                        continue

                    # For now, we chunk by lines or simple blocks
                    chunks = self._chunk_content(content)
                    for i, chunk in enumerate(chunks):
                        metadata = {
                            "filename": path.name,
                            "rel_path": str(path.relative_to(PROJECT_ROOT)),
                            "chunk_index": i,
                            "extension": path.suffix,
                        }

                        # Get real semantic embedding
                        vector = self._get_embedding(chunk)

                        mem.add_knowledge_chunk(
                            path=str(path.relative_to(PROJECT_ROOT)),
                            content=chunk,
                            metadata=metadata,
                            vector=vector,
                        )
                except Exception as e:
                    logger.error(f"Failed to index {path}: {e}")

    def _chunk_content(self, content: str, chunk_size: int = 1000) -> List[str]:
        """
        Line-aware chunking strategy to preserve code block integrity.
        """
        lines = content.splitlines()
        chunks = []
        current_chunk = []
        current_size = 0
        
        for line in lines:
            # If adding this line exceeds chunk_size, finalize current chunk
            if current_size + len(line) > chunk_size and current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_size = 0
            
            current_chunk.append(line)
            current_size += len(line) + 1 # +1 for newline
            
        if current_chunk:
            chunks.append("\n".join(current_chunk))
            
        return chunks

    def _get_embedding(self, text: str) -> List[float]:
        return get_text_embedding(text)


def get_text_embedding(text: str) -> List[float]:
    """
    Generates semantic embeddings using sentence-transformers.
    Falls back to a deterministic placeholder if the model is unavailable.
    """
    model = get_embedding_model()
    if model:
        try:
            # model.encode returns a numpy array
            vec = model.encode(text)
            return vec.tolist()
        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")

    # Fallback (384 dimensions to match MiniLM schema)
    import hashlib

    h = hashlib.sha256(text.encode()).digest()
    vec = [0.0] * 384
    for i in range(min(384, len(h))):
        vec[i] = float(h[i]) / 255.0
    return vec

if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    scanner = TerritoryScanner()
    asyncio.run(scanner.scan_all())
