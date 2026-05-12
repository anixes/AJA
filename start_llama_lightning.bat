@echo off
:: LIGHTNING CONFIG - OPTIMIZED FOR AGENTIC PP (70k+ Tokens)
:: Tuned for high efficiency on consumer-grade hardware
:: Focus: Fast initial prompt processing & 128k Stability

SET "SERVER_BIN=llama-server.exe"
SET "MODEL_PATH=path\to\your\model.gguf"

echo [LIGHTNING] Optimizing GPU clocks (if available)...
:: Optional optimization commands

echo [LIGHTNING] Launching Gemma-4 with Max Batching for Fast PP...
"%SERVER_BIN%" ^
  -m "%MODEL_PATH%" ^
  -ngl 100 ^
  -t 8 ^
  -c 131072 ^
  -fa on ^
  -ctk q4_0 ^
  -ctv q4_0 ^
  -b 1024 ^
  -ub 128 ^
  --context-shift ^
  --cache-prompt ^
  --temp 0.0 ^
  --port 8080 ^
  --host 0.0.0.0 ^
  --reasoning auto ^
  --reasoning-budget 1024

pause
