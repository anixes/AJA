import logging
import os
import uuid
import httpx
from pathlib import Path
from typing import Optional
from agentx.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

class VisionBridge:
    """
    Enriches incoming media with semantic descriptions.
    Enables 'Vision-to-Text' bridge for the AJA Gateway.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            cache_dir = os.path.join(PROJECT_ROOT, ".agentx", "gateway", "cache", "images")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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
