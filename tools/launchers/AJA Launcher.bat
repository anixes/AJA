@echo off
cd /d "%~dp0"
TITLE AJA Master Launcher
:: =======================================================
:: AJA Master Launcher (Optimized for High Efficiency)
:: Launches: Llama Gold Server + API Bridge + Dashboard
:: =======================================================

echo [MASTER] Verifying environment...
if not exist "node_modules" (
    echo [ERROR] node_modules not found. Running npm install...
    call npm install
)

:: Ensure models directory exists for the local LLM
if not exist "models" mkdir models

echo [MASTER] Starting Llama Gold Server (Optimized Backend)...
:: Start the model in a new window from the root directory
pushd "%~dp0..\.."
start "Llama Gold Server" cmd /k "start_llama_gold.bat"
popd

echo [MASTER] Waiting for model to initialize (15s)...
timeout /t 15 /nobreak >nul

echo [MASTER] Starting AJA Hardened v2.0 Swarm (Gateway + Worker + Dashboard)...
:: Set PYTHONPATH so the components can find aja-core
set "PYTHONPATH=%~dp0..\..\libs\aja-core;%PYTHONPATH%"

:: Launch AJA in a new window. This starts Gateway + Worker + Dashboard.
:: Note: npm run aja is executed from the ROOT, but we are in tools/launchers.
pushd "%~dp0..\.."
start "AJA Hardened Swarm" cmd /k "npm run aja"
popd

echo [MASTER] AJA v2.0 Swarm is initializing. 
echo [MASTER] Gateway is connecting to Telegram...
echo [MASTER] Worker is booting autonomous terminal control...
echo [MASTER] You can close THIS window now.

pause
