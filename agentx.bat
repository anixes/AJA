@echo off
REM =======================================================
REM AgentX Unified Launcher
REM Provides global 'agentx' command
REM =======================================================

set PYTHONPATH=%~dp0packages\agentx-core;%PYTHONPATH%

:: Load .env to get the correct Python interpreter
if exist "%~dp0.env" (
    for /f "tokens=*" %%a in ('findstr /v "^#" "%~dp0.env"') do set %%a
)

if "%PYTHON%"=="" (
    set "PYTHON=python"
)

"%PYTHON%" -m agentx %*
