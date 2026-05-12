@echo off
SETLOCAL EnableDelayedExpansion

:: --- PERFORMANCE OVERRIDES ---
SET "GGML_CUDA_FORCE_MMQ=1"

:: --- HARDWARE LOCKS ---
echo [TURBO] Optimizing GPU clocks (if available)...
:: Optional optimization commands

:: --- CONFIGURATION ---
SET "MODEL_PATH=path\to\your\model.gguf"
SET "LLAMA_BIN=llama-server.exe"

:: --- PREFILL TEST SETTINGS ---
:: -c 32768        : Match Gold's context
:: -t 8            : 8 Threads
SET "FLAGS=-np 1 -c 32768 -ngl 100 -b 512 -ub 64 -t 8 -ctk turbo4 -ctv turbo4 --cache-prompt --context-shift"

echo [TURBO] Launching Gemma 4 (32k Prefill Test)...
echo [TURBO] Mode: MMQ Optimized ^| Context: 32k ^| Kernel: turbo4
echo.

"%LLAMA_BIN%" -m "%MODEL_PATH%" %FLAGS% --port 8080 --host 0.0.0.0

pause
