@echo off
REM =======================================================
REM AgentX Unified Launcher
REM Provides global 'agentx' command
REM =======================================================

set PYTHONPATH=%~dp0packages\agentx-core;%PYTHONPATH%
python -m agentx %*
