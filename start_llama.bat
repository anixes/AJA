@echo off
set "MODEL_PATH=E:\llama_models\gemma-4-E2B-it-Q4_K_M.gguf"
set "DRAFT_PATH=E:\llama_models\gemma-3-270m-it-Q4_K_M.gguf"
set "CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.2\bin"
set "LLAMA_BIN=C:\Users\Asus\.gemini\antigravity\scratch\llama-bin"

:: Ensure the server and CUDA DLLs are in the path
set "PATH=%LLAMA_BIN%;%CUDA_PATH%;%PATH%"

echo Starting llama-server (128K AGENTIC MODE)...
echo Main Model: %MODEL_PATH%
echo Draft Model: %DRAFT_PATH%
echo VRAM Target: 4GB (GTX 1650 Ti)

:: 128K RESTORATION SETTINGS
:: -c 131072: Back to full 128k context
:: -ub 128: Small batch size to reclaim 1.2GB VRAM from compute buffer
:: -md: Draft model for instant response
:: -ngld 100: Draft model fully on GPU
:: --reasoning auto: Smart reasoning enabled

llama-server.exe ^
  -m "%MODEL_PATH%" ^
  -md "%DRAFT_PATH%" ^
  --host 0.0.0.0 ^
  --port 8080 ^
  -ngl 100 ^
  -ngld 100 ^
  -c 131072 ^
  -np 1 ^
  -t 8 ^
  -tb 16 ^
  -fa on ^
  -ctk q4_0 ^
  -ctv q4_0 ^
  -b 128 ^
  -ub 128 ^
  --no-mmproj ^
  --prio 3 ^
  --reasoning auto ^
  --cache-prompt ^
  --slot-save-path "C:\Users\Asus\.gemini\antigravity\scratch\llama-slots" ^
  --cache-reuse 256
