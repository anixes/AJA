@echo off
:: GOLD STANDARD - 51.27 TPS RESTORATION (NO DRAFTER)
:: Based on the peak performance recorded in speed_demon.log.
:: REQUIRED: Run as ADMINISTRATOR for GPU clock locking.

:: Try to load from .env using absolute path
if exist "%~dp0.env" (
    for /f "usebackq tokens=*" %%a in ("%~dp0.env") do (
        echo %%a | findstr /v "^#" >nul && set "%%a"
    )
)

:: Set defaults if not provided in .env
if "%LLAMA_SERVER_BIN%"=="" SET "LLAMA_SERVER_BIN=%USERPROFILE%\.gemini\antigravity\scratch\llama-bin\llama-server.exe"
if "%LLAMA_MODEL_PATH%"=="" SET "LLAMA_MODEL_PATH=E:\llama_models\gemma-4-E2B-it-Q4_K_M.gguf"

echo [GOLD] Optimizing GPU clocks (if available)...
:: Optional optimization commands

echo [GOLD] Launching Model: "%LLAMA_MODEL_PATH%"
echo [GOLD] Server Binary: "%LLAMA_SERVER_BIN%"

:: Check if binary exists
if not exist "%LLAMA_SERVER_BIN%" (
    where "%LLAMA_SERVER_BIN%" >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] "%LLAMA_SERVER_BIN%" not found in PATH or current directory.
        echo Please fix LLAMA_SERVER_BIN in your .env file.
        pause
        exit /b 1
    )
)

:: Check if model exists
if not exist "%LLAMA_MODEL_PATH%" (
    echo [ERROR] Model file not found: "%LLAMA_MODEL_PATH%"
    echo Please fix LLAMA_MODEL_PATH in your .env file.
    pause
    exit /b 1
)

:: Launch with optimized parameters
"%LLAMA_SERVER_BIN%" ^
  -m "%LLAMA_MODEL_PATH%" ^
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
