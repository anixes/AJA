@echo off
:: GOLD STANDARD - 51.27 TPS RESTORATION (NO DRAFTER)
:: Based on the peak performance recorded in speed_demon.log.
:: REQUIRED: Run as ADMINISTRATOR for GPU clock locking.

SET "SERVER_BIN=C:\Users\Asus\.gemini\antigravity\scratch\llama-bin\llama-server.exe"
SET "MODEL_PATH=E:\llama_models\gemma-4-E2B-it-Q4_K_M.gguf"

echo [GOLD] Locking GPU clocks for maximum throughput...
nvidia-smi -pm 1 >nul 2>&1
nvidia-smi -lgc 1500,1800 >nul 2>&1

echo [GOLD] Launching Gemma-4 (4.6B) Gold Standard (Tuned for 1650 Ti)...
"%SERVER_BIN%" ^
  -m "%MODEL_PATH%" ^
  -ngl 100 ^
  -t 8 ^
  -np 1 ^
  -c 32768 ^
  -fa on ^
  -ctk q4_0 ^
  -ctv q4_0 ^
  -b 512 ^
  -ub 256 ^
  --context-shift ^
  --cache-prompt ^
  --temp 0.0 ^
  --port 8080 ^
  --host 0.0.0.0 ^
  --reasoning auto ^
  --reasoning-budget 1024

pause
