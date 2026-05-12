@echo off
:: GOLD STANDARD - 51.27 TPS RESTORATION (NO DRAFTER)
:: Based on the peak performance recorded in speed_demon.log.
:: REQUIRED: Run as ADMINISTRATOR for GPU clock locking.

SET "SERVER_BIN=llama-server.exe"
SET "MODEL_PATH=path\to\your\model.gguf"

echo [GOLD] Optimizing GPU clocks (if available)...
:: Optional optimization commands

echo [GOLD] Launching Model - Gold Standard Configuration...
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
  -ub 64 ^
  --context-shift ^
  --cache-prompt ^
  --temp 0.0 ^
  --port 8080 ^
  --host 0.0.0.0 ^
  --reasoning auto ^
  --reasoning-budget 1024

pause
