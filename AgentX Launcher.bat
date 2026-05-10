@echo off
TITLE AgentX Master Launcher
:: =======================================================
:: AgentX Master Launcher (Optimized for GTX 1650 Ti)
:: Launches: Llama Gold Server + API Bridge + Dashboard
:: =======================================================

echo [MASTER] Starting Llama Gold Server (Optimized Backend)...
:: Start the model in a new window so it doesn't block this script
start "Llama Gold Server" cmd /c "start_llama_gold.bat"

echo [MASTER] Waiting for model to initialize...
timeout /t 5 /nobreak >nul

echo [MASTER] Starting AJA Services (API, Dashboard, Telegram)...
:: Launch the AJA ecosystem
npm run aja

pause
