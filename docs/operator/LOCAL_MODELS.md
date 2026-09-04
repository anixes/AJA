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

## 3. Configuration

In your `aja.json`:
```json
{
  "operating_mode": "hybrid",
  "local_backend": "llama_cpp",
  "models": {
    "worker": "llama_cpp:qwen2.5-coder-7b-instruct-q3_k_m"
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
