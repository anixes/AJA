@echo off
cd /d "%~dp0"
TITLE AgentX Master Launcher
:: =======================================================
:: AgentX Master Launcher (Optimized for GTX 1650 Ti)
:: Launches: Llama Gold Server + API Bridge + Dashboard
:: =======================================================

echo [MASTER] Verifying environment...
if not exist "node_modules" (
    echo [ERROR] node_modules not found. Running npm install...
    call npm install
)

echo [MASTER] Starting Llama Gold Server (Optimized Backend)...
:: Start the model in a new window
start "Llama Gold Server" cmd /c "start_llama_gold.bat"

echo [MASTER] Waiting for model to initialize (15s)...
:: Increased timeout for 1650 Ti to ensure weights are fully loaded
timeout /t 15 /nobreak >nul

echo [MASTER] Starting AJA Services (API, Dashboard, Telegram)...
:: Set PYTHONPATH so the bridge can find agentx-core
set "PYTHONPATH=%CD%\packages\agentx-core;%PYTHONPATH%"

:: Launch AJA in a new window so it doesn't block and logs are visible
start "AgentX AJA Services" cmd /k "npm run aja"

echo [MASTER] AgentX Swarm is initializing. 
echo [MASTER] Check the separate windows for Llama Server and AJA logs.
echo [MASTER] You can close THIS window now.

pause
