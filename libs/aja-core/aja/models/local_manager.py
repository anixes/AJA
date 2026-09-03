"""
aja.models.local_manager — Discovery, health checks, and activation for local models.
=====================================================================================
Supports Ollama, llama.cpp, LM Studio, and generic OpenAI-compatible local servers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aja.config import DATA_DIR


@dataclass
class LocalModelInfo:
    name: str
    engine: str  # "ollama", "llama_cpp", "lm_studio", "custom"
    uri: str     # e.g. "ollama:qwen2.5-coder:7b"
    size_gb: Optional[float] = None
    parameter_size: Optional[str] = None
    quantization: Optional[str] = None
    modified_at: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EngineStatus:
    name: str
    endpoint: str
    running: bool
    installed: bool
    models_count: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LocalModelManager:
    """Manages local model detection, health checking, and configuration."""

    DEFAULT_ENDPOINTS = {
        "ollama": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "llama_cpp": os.getenv("LLAMA_CPP_URL", "http://localhost:8080"),
        "lm_studio": os.getenv("LM_STUDIO_URL", "http://localhost:1234"),
    }

    RECOMMENDED_MODELS = [
        {"name": "qwen2.5-coder:7b", "engine": "ollama", "description": "Top-tier compact coding agent model (4.7 GB)"},
        {"name": "deepseek-r1:8b", "engine": "ollama", "description": "Powerful reasoning and planning model (4.9 GB)"},
        {"name": "llama3.2:3b", "engine": "ollama", "description": "Ultra-fast, lightweight 3B agent model (2.0 GB)"},
        {"name": "qwen2.5-coder:1.5b", "engine": "ollama", "description": "Low-spec CPU friendly agent model (1.0 GB)"},
    ]

    @classmethod
    def _fetch_json(cls, url: str, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "AJA-LocalModelManager/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        return None

    @classmethod
    def probe_engines(cls, timeout: float = 1.0) -> Dict[str, EngineStatus]:
        """Check availability of each supported local model engine."""
        statuses: Dict[str, EngineStatus] = {}

        # 1. Ollama
        ollama_endpoint = cls.DEFAULT_ENDPOINTS["ollama"]
        base_ollama = ollama_endpoint.replace("/v1", "").rstrip("/")
        ollama_installed = bool(shutil.which("ollama"))
        ollama_data = cls._fetch_json(f"{base_ollama}/api/tags", timeout=timeout)

        if ollama_data and "models" in ollama_data:
            statuses["ollama"] = EngineStatus(
                name="Ollama",
                endpoint=base_ollama,
                running=True,
                installed=ollama_installed or True,
                models_count=len(ollama_data["models"]),
            )
        else:
            statuses["ollama"] = EngineStatus(
                name="Ollama",
                endpoint=base_ollama,
                running=False,
                installed=ollama_installed,
                error="Service not responding on port 11434",
            )

        # 2. llama.cpp / llama-server
        llama_endpoint = cls.DEFAULT_ENDPOINTS["llama_cpp"].rstrip("/")
        llama_installed = bool(shutil.which("llama-server") or shutil.which("llama.cpp"))
        llama_data = cls._fetch_json(f"{llama_endpoint}/v1/models", timeout=timeout)

        if llama_data and "data" in llama_data:
            statuses["llama_cpp"] = EngineStatus(
                name="llama.cpp",
                endpoint=llama_endpoint,
                running=True,
                installed=llama_installed or True,
                models_count=len(llama_data.get("data", [])),
            )
        else:
            statuses["llama_cpp"] = EngineStatus(
                name="llama.cpp",
                endpoint=llama_endpoint,
                running=False,
                installed=llama_installed,
                error="Service not responding on port 8080",
            )

        # 3. LM Studio
        lms_endpoint = cls.DEFAULT_ENDPOINTS["lm_studio"].rstrip("/")
        lms_data = cls._fetch_json(f"{lms_endpoint}/v1/models", timeout=timeout)
        if lms_data and "data" in lms_data:
            statuses["lm_studio"] = EngineStatus(
                name="LM Studio",
                endpoint=lms_endpoint,
                running=True,
                installed=True,
                models_count=len(lms_data.get("data", [])),
            )
        else:
            statuses["lm_studio"] = EngineStatus(
                name="LM Studio",
                endpoint=lms_endpoint,
                running=False,
                installed=False,
                error="Service not responding on port 1234",
            )

        return statuses

    @classmethod
    def discover_models(cls, timeout: float = 1.5) -> List[LocalModelInfo]:
        """Scan all running local engines and return all available models."""
        discovered: List[LocalModelInfo] = []

        # 1. Probe Ollama
        ollama_endpoint = cls.DEFAULT_ENDPOINTS["ollama"].replace("/v1", "").rstrip("/")
        data = cls._fetch_json(f"{ollama_endpoint}/api/tags", timeout=timeout)
        if data and "models" in data:
            for item in data["models"]:
                raw_name = item.get("name", "")
                size_bytes = item.get("size", 0)
                size_gb = round(size_bytes / (1024 ** 3), 2) if size_bytes else None

                details = item.get("details", {})
                param_size = details.get("parameter_size")
                quant = details.get("quantization_level")

                discovered.append(
                    LocalModelInfo(
                        name=raw_name,
                        engine="ollama",
                        uri=f"ollama:{raw_name}",
                        size_gb=size_gb,
                        parameter_size=param_size,
                        quantization=quant,
                        modified_at=item.get("modified_at"),
                        details=item,
                    )
                )

        # 2. Probe llama.cpp
        llama_endpoint = cls.DEFAULT_ENDPOINTS["llama_cpp"].rstrip("/")
        llama_data = cls._fetch_json(f"{llama_endpoint}/v1/models", timeout=timeout)
        if llama_data and "data" in llama_data:
            for item in llama_data["data"]:
                m_id = item.get("id", "default")
                discovered.append(
                    LocalModelInfo(
                        name=m_id,
                        engine="llama_cpp",
                        uri=f"llama_cpp:{m_id}",
                        details=item,
                    )
                )

        # 3. Probe LM Studio
        lms_endpoint = cls.DEFAULT_ENDPOINTS["lm_studio"].rstrip("/")
        lms_data = cls._fetch_json(f"{lms_endpoint}/v1/models", timeout=timeout)
        if lms_data and "data" in lms_data:
            for item in lms_data["data"]:
                m_id = item.get("id", "")
                discovered.append(
                    LocalModelInfo(
                        name=m_id,
                        engine="lm_studio",
                        uri=f"openai:{m_id}",
                        details=item,
                    )
                )

        return discovered

    @classmethod
    def start_engine(cls, engine: str = "ollama") -> Tuple[bool, str]:
        """Attempt to start a local model server daemon if installed."""
        if engine == "ollama":
            if not shutil.which("ollama"):
                return False, "Ollama executable not found in PATH. Install from https://ollama.com."
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                return True, "Started 'ollama serve' in background."
            except Exception as e:
                return False, f"Failed to launch Ollama: {e}"

        return False, f"Automated startup for engine '{engine}' is not supported. Start it manually."

    @classmethod
    def activate_model(cls, model_uri: str, role: str = "worker") -> bool:
        """
        Persist model selection to aja.json and update runtime configuration.
        Also sets operating_mode='hybrid' to ensure local inference routes directly.
        """
        try:
            cfg_path = DATA_DIR / "aja.json"
            data: Dict[str, Any] = {}
            if cfg_path.exists():
                try:
                    with open(cfg_path, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}

            if "swarm_settings" not in data:
                data["swarm_settings"] = {}
            if "models" not in data["swarm_settings"]:
                data["swarm_settings"]["models"] = {}

            # Update role model
            data["swarm_settings"]["models"][role] = model_uri
            # Ensure operating_mode is hybrid so local model won't be redirected to cloud
            data["swarm_settings"]["operating_mode"] = "hybrid"

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            # Update live config
            import aja.config
            if role == "worker":
                aja.config.AJA_WORKER_MODEL = model_uri
            elif role == "planner":
                aja.config.AJA_PLANNER_MODEL = model_uri

            return True
        except Exception as e:
            print(f"[LocalModelManager] Failed to activate model '{model_uri}': {e}")
            return False

    @classmethod
    def get_active_model(cls) -> Dict[str, str]:
        """Return currently active planner and worker models."""
        from aja.config import AJA_PLANNER_MODEL, AJA_WORKER_MODEL
        return {
            "planner": AJA_PLANNER_MODEL,
            "worker": AJA_WORKER_MODEL,
        }
