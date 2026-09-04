"""
aja.models.local_manager — Discovery, health checks, and activation for local models.
=====================================================================================
Supports llama.cpp, Ollama, LM Studio, and generic OpenAI-compatible local servers.
Includes native scanning of local GGUF model directories and automated CUDA llama-server launch.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aja.config import DATA_DIR


@dataclass
class HostHardwareProfile:
    os_name: str
    os_release: str
    cpu_model: str
    cpu_cores: int
    ram_total_gb: float
    ram_available_gb: float
    gpu_name: Optional[str] = None
    vram_total_mb: Optional[int] = None
    vram_free_mb: Optional[int] = None
    driver_version: Optional[str] = None
    has_cuda: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HostHardwareProfiler:
    """Zero-dependency hardware inspection for CPU, RAM, and NVIDIA CUDA GPU."""
    _cached_profile: Optional[HostHardwareProfile] = None
    _cached_at: float = 0.0

    @classmethod
    def get_profile(cls, force_refresh: bool = False) -> HostHardwareProfile:
        now = time.time()
        if not force_refresh and cls._cached_profile and (now - cls._cached_at < 30.0):
            return cls._cached_profile

        import platform
        os_name = platform.system()
        os_release = platform.release()
        cpu_model = platform.processor() or "Unknown CPU"
        cpu_cores = os.cpu_count() or 1
        ram_total_gb = 8.0
        ram_avail_gb = 4.0

        if os_name == "Windows":
            try:
                import ctypes
                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [
                        ('dwLength', ctypes.c_ulong),
                        ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong),
                        ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong),
                        ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong),
                        ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('sullAvailExtendedVirtual', ctypes.c_ulonglong),
                    ]
                stat = MEMORYSTATUSEX()
                stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                    ram_total_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
                    ram_avail_gb = round(stat.ullAvailPhys / (1024 ** 3), 1)
            except Exception:
                pass
        else:
            try:
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    meminfo = f.read()
                for line in meminfo.splitlines():
                    if line.startswith("MemTotal:"):
                        ram_total_gb = round(int(line.split()[1]) / (1024 ** 2), 1)
                    elif line.startswith("MemAvailable:"):
                        ram_avail_gb = round(int(line.split()[1]) / (1024 ** 2), 1)
            except Exception:
                pass

        gpu_name: Optional[str] = None
        vram_total_mb: Optional[int] = None
        vram_free_mb: Optional[int] = None
        driver_version: Optional[str] = None
        has_cuda = False

        try:
            smi = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            if smi.returncode == 0 and smi.stdout.strip():
                first_line = smi.stdout.strip().split("\n")[0]
                parts = [p.strip() for p in first_line.split(",")]
                if len(parts) >= 4:
                    gpu_name = parts[0]
                    vram_total_mb = int(float(parts[1]))
                    vram_free_mb = int(float(parts[2]))
                    driver_version = parts[3]
                    has_cuda = True
        except Exception:
            pass

        profile = HostHardwareProfile(
            os_name=os_name,
            os_release=os_release,
            cpu_model=cpu_model,
            cpu_cores=cpu_cores,
            ram_total_gb=ram_total_gb,
            ram_available_gb=ram_avail_gb,
            gpu_name=gpu_name,
            vram_total_mb=vram_total_mb,
            vram_free_mb=vram_free_mb,
            driver_version=driver_version,
            has_cuda=has_cuda,
        )
        cls._cached_profile = profile
        cls._cached_at = now
        return profile


@dataclass
class LocalModelInfo:
    name: str
    engine: str  # "ollama", "llama_cpp", "lm_studio", "custom"
    uri: str     # e.g. "ollama:qwen2.5-coder:7b", "llama_cpp:qwen2.5-coder-7b.gguf"
    size_gb: Optional[float] = None
    parameter_size: Optional[str] = None
    quantization: Optional[str] = None
    modified_at: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    auto_tuned_ngl: Optional[int] = None
    recommendation: Optional[str] = None

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

    DEFAULT_MODELS_DIRS: List[Path] = [
        Path(os.getenv("AJA_MODELS_DIR", "E:/Models")),
        Path("E:/Models"),
        Path("./models"),
    ]

    DEFAULT_LLAMA_SERVER_PATHS: List[Path] = [
        Path("E:/Llama-Turbo-Bin/llama-server.exe"),
        Path("E:/Bonsai-demo/bin/cuda/llama-server.exe"),
        Path("E:/.gemini/antigravity-ide/scratch/llama-bin/llama-server.exe"),
    ]

    RECOMMENDED_MODELS = [
        {"name": "qwen2.5-coder-7b-instruct-q3_k_m.gguf", "engine": "llama_cpp", "description": "Top-tier agent coding model (3.8 GB)"},
        {"name": "gemma-4-E2B-it-Q4_K_M.gguf", "engine": "llama_cpp", "description": "Ultra-fast full-VRAM agent model (2.9 GB)"},
        {"name": "qwen2.5-coder:7b", "engine": "ollama", "description": "Compact coding agent model (4.7 GB)"},
        {"name": "deepseek-r1:8b", "engine": "ollama", "description": "Powerful reasoning and planning model (4.9 GB)"},
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
    def get_hardware_profile(cls) -> HostHardwareProfile:
        """Inspect host machine CPU, RAM, and GPU hardware."""
        return HostHardwareProfiler.get_profile()

    @classmethod
    def find_llama_server_binary(cls) -> Optional[Path]:
        """Resolve the path to an installed llama-server executable across drives and PATH."""
        env_path = os.getenv("LLAMA_SERVER_PATH")
        if env_path and Path(env_path).is_file():
            return Path(env_path)

        # 1. System PATH
        system_bin = shutil.which("llama-server") or shutil.which("llama-server.exe")
        if system_bin:
            return Path(system_bin)

        # 2. Known candidates
        for candidate in cls.DEFAULT_LLAMA_SERVER_PATHS:
            if candidate.is_file():
                return candidate

        # 3. Dynamic multi-drive scan across Windows drives
        if os.name == "nt":
            for letter in ["C", "D", "E", "F"]:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    for sub in ["Llama-Turbo-Bin", "llama.cpp", "llama-bin", "bin", "tools/llama.cpp", "Bonsai-demo/bin/cuda"]:
                        p = drive / sub / "llama-server.exe"
                        if p.exists() and p.is_file():
                            return p

        return None

    @classmethod
    def discover_model_directories(cls) -> List[Path]:
        """Discover all directories across host drives that contain AI models."""
        dirs: List[Path] = []
        seen = set()

        def add_dir(p: Path):
            try:
                resolved = p.resolve()
                key = str(resolved).lower()
                if resolved.exists() and resolved.is_dir() and key not in seen:
                    seen.add(key)
                    dirs.append(resolved)
            except Exception:
                pass

        env_dir = os.getenv("AJA_MODELS_DIR")
        if env_dir:
            add_dir(Path(env_dir))

        for d in cls.DEFAULT_MODELS_DIRS:
            add_dir(d)

        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                if drive.exists():
                    for sub in ["Models", "models", "LLM", "llm", "GGUF", "gguf"]:
                        add_dir(drive / sub)

        try:
            home = Path.home()
            for sub in [".ollama/models", ".cache/lm-studio/models", ".cache/huggingface/hub", "models", "Models"]:
                add_dir(home / sub)
        except Exception:
            pass

        return dirs

    @classmethod
    def scan_disk_gguf_models(cls, directory: Optional[Path] = None) -> List[LocalModelInfo]:
        """Scan local disk directories across host for .gguf model files and extract metadata."""
        target_dirs: List[Path] = [directory] if directory else cls.discover_model_directories()
        models: List[LocalModelInfo] = []
        seen_files = set()

        quant_pattern = re.compile(r"(Q[0-9]_[K0-9_MSAL]+|F16|F32|Q8_0)", re.IGNORECASE)
        param_pattern = re.compile(r"([0-9.]+B|E[0-9]+B)", re.IGNORECASE)

        hw = cls.get_hardware_profile()
        vram_gb = (hw.vram_total_mb / 1024.0) if hw.vram_total_mb else 4.0

        for d in target_dirs:
            if not d.exists() or not d.is_dir():
                continue
            try:
                for entry in d.glob("*.gguf"):
                    if entry.name.lower().startswith("mmproj"):
                        continue

                    res_path = str(entry.resolve())
                    if res_path.lower() in seen_files:
                        continue
                    seen_files.add(res_path.lower())

                    size_bytes = entry.stat().st_size
                    size_gb = round(size_bytes / (1024 ** 3), 2)

                    q_match = quant_pattern.search(entry.name)
                    p_match = param_pattern.search(entry.name)
                    quant = q_match.group(1).upper() if q_match else None
                    param = p_match.group(1).upper() if p_match else None

                    # Hardware-aware offload auto-tuning
                    auto_ngl = 99
                    rec = "Local GGUF"
                    name_lower = entry.name.lower()

                    if size_gb <= (vram_gb - 0.8):
                        auto_ngl = 99
                        rec = "⚡ 100% GPU VRAM (fastest, ~60+ tok/s)"
                    elif size_gb <= (vram_gb + 2.0):
                        auto_ngl = 28
                        rec = "⭐ Recommended Coding Worker (hybrid CUDA offload)"
                    else:
                        auto_ngl = 16
                        rec = "CPU / Partial offload (large model)"

                    if "vl" in name_lower or "vision" in name_lower:
                        rec += " · 👁️ Multimodal Vision"
                    elif "coder" in name_lower or "qwen" in name_lower:
                        rec += " · 💻 Agent CodeAct"

                    models.append(
                        LocalModelInfo(
                            name=entry.name,
                            engine="llama_cpp",
                            uri=f"llama_cpp:{entry.name}",
                            size_gb=size_gb,
                            parameter_size=param,
                            quantization=quant,
                            auto_tuned_ngl=auto_ngl,
                            recommendation=rec,
                            details={"path": res_path, "source": "disk"},
                        )
                    )
            except Exception:
                continue

        models.sort(key=lambda m: m.size_gb or 0.0, reverse=True)
        return models

    @classmethod
    def scan_host_models(cls) -> List[LocalModelInfo]:
        """Alias for scan_disk_gguf_models scanning all discovered host directories."""
        return cls.scan_disk_gguf_models()

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
                installed=True,
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
        server_bin = cls.find_llama_server_binary()
        llama_installed = bool(server_bin is not None)
        llama_data = cls._fetch_json(f"{llama_endpoint}/v1/models", timeout=timeout)

        # Also count disk GGUFs
        disk_ggufs = cls.scan_disk_gguf_models()

        if llama_data and "data" in llama_data:
            statuses["llama_cpp"] = EngineStatus(
                name="llama.cpp",
                endpoint=llama_endpoint,
                running=True,
                installed=True,
                models_count=len(llama_data.get("data", [])),
            )
        else:
            err_msg = "Service not responding on port 8080"
            if disk_ggufs:
                err_msg += f" ({len(disk_ggufs)} GGUF models ready on disk)"
            statuses["llama_cpp"] = EngineStatus(
                name="llama.cpp",
                endpoint=llama_endpoint,
                running=False,
                installed=llama_installed,
                models_count=len(disk_ggufs),
                error=err_msg,
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
    def discover_models(cls, timeout: float = 1.0, include_disk: bool = True) -> List[LocalModelInfo]:
        """Scan running local engines and local disk directories for all available models."""
        discovered: List[LocalModelInfo] = []
        running_names = set()

        # 1. Probe running Ollama
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

        # 2. Probe running llama.cpp
        llama_endpoint = cls.DEFAULT_ENDPOINTS["llama_cpp"].rstrip("/")
        llama_data = cls._fetch_json(f"{llama_endpoint}/v1/models", timeout=timeout)
        if llama_data and "data" in llama_data:
            for item in llama_data["data"]:
                m_id = item.get("id", "default")
                running_names.add(m_id)
                discovered.append(
                    LocalModelInfo(
                        name=m_id,
                        engine="llama_cpp (active)",
                        uri=f"llama_cpp:{m_id}",
                        details=item,
                    )
                )

        # 3. Probe running LM Studio
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

        # 4. Scan disk GGUFs
        if include_disk:
            disk_models = cls.scan_disk_gguf_models()
            for dm in disk_models:
                if dm.name not in running_names:
                    discovered.append(dm)

        return discovered

    @classmethod
    def start_llama_server(
        cls,
        model: Path | str,
        port: int = 8080,
        ngl: Optional[int] = None,
        ctx: int = 8192,
    ) -> Tuple[bool, str]:
        """Launch llama-server.exe in background with GPU acceleration and Jinja tool support."""
        server_bin = cls.find_llama_server_binary()
        if not server_bin:
            return False, "Could not locate llama-server.exe on system."

        model_path: Optional[Path] = None
        if isinstance(model, Path) and model.is_file():
            model_path = model
        else:
            # Match by filename against disk models
            raw_str = str(model).replace("llama_cpp:", "")
            for dm in cls.scan_disk_gguf_models():
                if dm.name == raw_str or dm.name.startswith(raw_str):
                    model_path = Path(dm.details["path"])
                    break
            if model_path is None and Path(raw_str).is_file():
                model_path = Path(raw_str)

        if not model_path or not model_path.is_file():
            return False, f"Model file not found: '{model}'"

        # Auto-tune GPU offload layers (-ngl) dynamically using host hardware VRAM
        if ngl is None:
            hw = cls.get_hardware_profile()
            vram_gb = (hw.vram_total_mb / 1024.0) if hw.vram_total_mb else 4.0
            size_gb = model_path.stat().st_size / (1024 ** 3)
            if size_gb <= (vram_gb - 0.8):
                ngl = 99  # Full offload for models fitting completely in VRAM
            elif size_gb <= (vram_gb + 2.0):
                ngl = 28  # Safe partial offload for 7B models
            else:
                ngl = 16

        cmd = [
            str(server_bin),
            "-m", str(model_path.resolve()),
            "--port", str(port),
            "-ngl", str(ngl),
            "-c", str(ctx),
            "--jinja",
        ]

        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )

            # Health poll up to 4 seconds
            endpoint = f"http://localhost:{port}"
            for _ in range(8):
                time.sleep(0.5)
                res = cls._fetch_json(f"{endpoint}/v1/models", timeout=0.5)
                if res and "data" in res:
                    return True, f"Successfully started llama-server with '{model_path.name}' on port {port} (GPU layers: {ngl})."

            return True, f"Launched llama-server process (verifying initialization on port {port})."
        except Exception as e:
            return False, f"Failed to launch llama-server: {e}"

    @classmethod
    def stop_llama_server(cls) -> Tuple[bool, str]:
        """Stop running llama-server process across Windows and POSIX systems."""
        try:
            if os.name == "nt":
                res = subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, text=True)
                if res.returncode == 0:
                    return True, "Successfully stopped llama-server process."
                status = cls.probe_engines().get("llama_cpp")
                if not status or not status.running:
                    return False, "No active llama-server.exe process found."
                return False, f"Could not terminate process: {res.stderr.strip()}"
            else:
                res = subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, text=True)
                if res.returncode == 0:
                    return True, "Successfully stopped llama-server process."
                return False, "No running llama-server process found."
        except Exception as e:
            return False, f"Failed to stop llama-server: {e}"

    @classmethod
    def start_engine(cls, engine: str = "ollama", model: Optional[str] = None) -> Tuple[bool, str]:
        """Attempt to start a local model server daemon if installed."""
        eng_lower = engine.lower()
        if "llama" in eng_lower:
            if not model:
                # Default to best available coding GGUF on disk
                disk_models = cls.scan_disk_gguf_models()
                qwen = next((m for m in disk_models if "qwen" in m.name.lower()), None)
                target = qwen or (disk_models[0] if disk_models else None)
                if not target:
                    return False, "No .gguf models found in models directory to launch."
                model = target.name

            return cls.start_llama_server(model)

        elif eng_lower == "ollama":
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
        If pointing to an offline disk GGUF, launches llama-server automatically.
        """
        try:
            # Check if model is a local GGUF on disk and server is not running
            if model_uri.startswith("llama_cpp:"):
                raw_name = model_uri.replace("llama_cpp:", "")
                server_status = cls.probe_engines().get("llama_cpp")
                if not server_status or not server_status.running:
                    # Check if file exists on disk
                    for dm in cls.scan_disk_gguf_models():
                        if dm.name == raw_name:
                            cls.start_llama_server(dm.name)
                            break

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
