import logging
import os
import uuid
import httpx
from pathlib import Path
from typing import Optional
from aja.config import PROJECT_ROOT, DATA_DIR

logger = logging.getLogger(__name__)

class VisionBridge:
    """
    Enriches incoming media with semantic descriptions.
    Enables 'Vision-to-Text' bridge for the AJA Gateway.

    NOTE (unused-but-retained): VisionBridge is instantiated by the gateway
    orchestrator but its describe/download methods are NOT called anywhere in
    the live pipeline yet (describe_image returns a placeholder). It is kept
    as the future fallback describer for non-vision models. The on-disk image
    cache is bounded: at most ``MAX_CACHE_FILES`` files are retained; older
    files are swept on every write.
    """

    MAX_CACHE_FILES = 50

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            cache_dir = str(DATA_DIR / "gateway" / "cache" / "images")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _sweep_cache(self) -> None:
        """Best-effort eviction keeping only the newest MAX_CACHE_FILES files."""
        try:
            files = sorted(
                (p for p in self.cache_dir.glob("*") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for stale in files[self.MAX_CACHE_FILES:]:
                stale.unlink(missing_ok=True)
                logger.debug("Vision cache sweep removed %s", stale.name)
        except Exception as e:
            # best-effort: never fail an image capture over cache hygiene
            logger.debug("Vision cache sweep skipped: %s", e)

    async def describe_image(self, image_data: bytes, ext: str = ".jpg") -> str:
        """
        Processes an image and returns a text description.
        In the future, this will call a VLM (Vision Language Model).
        For now, it saves the image and returns a structured placeholder.
        """
        filename = f"aja_img_{uuid.uuid4().hex[:8]}{ext}"
        filepath = self.cache_dir / filename
        
        try:
            with open(filepath, "wb") as f:
                f.write(image_data)
            
            logger.info(f"AJA Gateway: Image cached at {filepath}")
            self._sweep_cache()
            
            # Placeholder for VLM integration
            # In a real scenario, we'd call Gemini/Claude-Vision here.
            return f"[AJA Vision Bridge: Captured image '{filename}'. Semantic description pending VLM connection.]"
        except Exception as e:
            logger.error(f"AJA Vision Bridge failure: {e}")
            return "[AJA Vision Bridge: Failed to process image.]"

    async def download_and_describe(self, url: str, headers: Optional[dict] = None) -> str:
        """Downloads an image from a URL and describes it."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return await self.describe_image(resp.content)
        except Exception as e:
            logger.error(f"AJA Vision download failure: {e}")
            return f"[AJA Vision Bridge: Failed to download image from {url}]"
