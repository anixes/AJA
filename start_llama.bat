@echo off
taskkill /F /IM llama-server.exe >nul 2>&1
set MODEL_PATH=E:\llama_models\gemma-4-E2B-it-Q4_K_M.gguf
set SERVER_PATH=C:\Users\Asus\.docker\bin\inference\llama-server.exe
echo Starting local llama-server with %MODEL_PATH% (128k Context Mode)...
:: Optimization for 128k context on 4GB VRAM:
:: -c 131072: Request full 128k context window.
:: -np 1: Essential to keep SWA overhead minimal.
:: -ctk q4_0 -ctv q4_0: 4-bit KV quantization to fit the massive cache.
:: -ngl 16: Offloading fewer layers (16/35) to GPU to make room for the 128k KV cache.
:: -fa on: Flash Attention for speed.
"%SERVER_PATH%" -m "%MODEL_PATH%" --host 0.0.0.0 --port 8080 -ngl 16 -c 131072 -np 1 --embedding --pooling mean -t 8 -fa on -ctk q4_0 -ctv q4_0
