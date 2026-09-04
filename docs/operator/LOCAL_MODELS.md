# Local Models & Offline llama.cpp Execution in AJA

AJA provides first-class support for offline, local execution using **`llama.cpp`** and **GGUF models** running on consumer GPUs (such as NVIDIA GeForce GTX 1650 Ti with 4GB VRAM) or CPUs.

---

## 1. Key Capabilities

* **GGUF Model Scanner**: Automatically detects GGUF models on disk (e.g. `E:\Models`), extracting parameter counts (`7B`, `E2B`, `1.6B`) and quantization types (`Q3_K_M`, `Q4_K_M`, `Q8_0`).
* **CUDA Auto-Launcher**: Automatically resolves `llama-server.exe` (e.g. from `E:\Llama-Turbo-Bin\llama-server.exe`), calculates optimal GPU offloading (`-ngl 99` for models $\le$ 3.2 GB; `-ngl 28` for 7B models on 4GB VRAM), and spawns the background server with Jinja ChatML templates (`--jinja`) and 8k context (`-c 8192`).
* **Native llama.cpp Vector Embeddings**: Direct vector computation via `http://localhost:8080/v1/embeddings`, bypassing heavy PyTorch / Hugging Face model downloads and consuming 0 MB extra RAM.
* **GBNF Grammar-Constrained Tool Calling**: Enforces GGML BNF (GBNF) grammar constraints during sampling, mathematically eliminating syntax, markdown formatting, and JSON parameter hallucination errors.

---

## 2. Quick Usage

### Interactive Model Selection
From the command line or REPL:
```powershell
# List available local models and auto-launch
aja local

# Inside the interactive chat REPL
/local
```

### Command Line Flags
```powershell
aja local ls                  # List discovered GGUF and Ollama models
aja local start <model_name>  # Start CUDA llama-server in background
aja local stop                # Stop running local llama-server
```

---

## 3. Operating Modes & Model-Agnostic Capability Routing

AJA has transitioned from rigid "Planner vs Worker" role models to a **Model-Agnostic Capability Router** with 4 Operating Modes:

| Mode | Icon | Description | Network Egress | Typical Hardware / Engine |
| :--- | :---: | :--- | :---: | :--- |
| **Local** | 🏠 | 100% on-device inference for all tasks. Cloud calls are redirected to local fallback. | **Zero** | llama.cpp (CUDA GTX 1650 Ti), Ollama |
| **Cloud** | ☁️ | 100% cloud model inference. Local model calls redirect to cloud fallback. | Enabled | Copilot (GPT-4o), Gemini 2.5 Flash, Claude |
| **Hybrid** | ⚡ | **Capability-driven auto-router**. Text reasoning uses the active model; multimodal image queries auto-route to the active local vision model (`LFM2.5-VL-1.6B`). | Selective | Mixed (Cloud reasoning + Local CUDA Vision) |
| **Swarm** | 🐝 | Autonomous background multi-agent missions with dedicated Planner-Worker-Critic sub-agents. | Variable | Configurable per sub-agent |

### Switching Modes
From Telegram or CLI:
* `/mode` — Displays the model and capability status card.
* `/mode local` — Switches operating mode to Local.
* `/mode cloud` — Switches operating mode to Cloud.
* `/mode hybrid` — Switches operating mode to Hybrid (auto-router).
* `/mode swarm` — Switches operating mode to Swarm.
* `aja local mode <local|cloud|hybrid|swarm>` — Command-line mode switcher.

### Multimodal Vision & mmproj Projector Auto-Discovery
For local vision models (e.g. `LFM2.5-VL-1.6B-Q4_K_M.gguf`), `llama-server` requires a matching multimodal projector (`--mmproj`).
AJA automatically scans the model directory for matching projectors (such as `E:\Models\mmproj-LFM2.5-VL-1.6B-F16.gguf`) and auto-attaches `--mmproj <path>` during engine launch.

### Configuration (`aja.json`)
```json
{
  "swarm_settings": {
    "operating_mode": "hybrid",
    "active_model": "llama_cpp:LFM2.5-VL-1.6B-Q4_K_M.gguf",
    "vision_model": "llama_cpp:LFM2.5-VL-1.6B-Q4_K_M.gguf",
    "models": {
      "planner": "copilot:gpt-4o",
      "worker": "llama_cpp:qwen2.5-coder-7b-instruct-q3_k_m.gguf"
    }
  },
  "embeddings": {
    "backend": "llama_cpp",
    "endpoint": "http://localhost:8080/v1/embeddings"
  }
}
```

---

## 4. GBNF Tool Grammar Architecture

When routing tool calls to `llama_cpp`, AJA's `aja.models.gbnf` compiler converts tool definitions into strict GBNF grammar rules:
```bnf
root ::= tool-call | text-message
tool-call ::= "{" ws "\"name\"" ws ":" ws func-name ws "," ws "\"arguments\"" ws ":" ws json-object ws "}"
func-name ::= "bash" | "read_file" | "write_file"
```

The compiled grammar is injected into `extra_body={"grammar": ...}` for `/v1/chat/completions`. The server constrains output tokens during sampling, ensuring 100% deterministic schema adherence.

---

## 5. Telegram Remote Controls & Host Hardware Profiling

AJA features complete mobile control over host GPU inference directly from Telegram:

* **Host Hardware Profiling**: Detects OS, CPU cores, RAM, and NVIDIA GPU/VRAM via zero-dependency standard library and `nvidia-smi` queries.
* **Multi-Drive GGUF Auto-Discovery**: Automatically scans all mounted drives (`C:`, `D:`, `E:`) and standard application caches (`~/.ollama/models`, `~/.cache/lm-studio/models`, `~/.cache/huggingface/hub`) for `.gguf` files.
* **Hardware-Aware Auto-Tuning**: Compares model file size with detected GPU VRAM to tag models as `100% GPU VRAM` (`-ngl 99`, ~60+ tok/s), `Recommended Coding Worker` (`-ngl 28`, hybrid CUDA offload), or `Multimodal Vision`.
* **Telegram Commands**:
  - `/local` or `/models`: Renders the full host hardware status card and interactive inline buttons.
  - `/local start <model>`: Launches the specified model on CUDA port 8080 and activates it as worker.
  - `/local stop`: Terminates the background `llama-server` process and releases GPU VRAM.
* **1-Tap Inline Keyboard Controls**:
  - `[▶ Start <model>]`: Launches and activates the model in one tap (`ls:<idx>` compact token).
  - `[⏹ Stop llama-server]`: Releases GPU VRAM on demand (`lstp`).
  - `[🔄 Rescan Host]`: Re-scans all drives and updates the Telegram card (`lref`).
* **Agent Native Tools**: The LLM itself has tool access to `inspect_host_hardware()` and `manage_local_models()` to inspect hardware specs and autonomously manage local inference engines from natural language.

