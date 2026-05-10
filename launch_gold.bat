@echo off
pushd "%~dp0"
echo [LAUNCHER] Switched to %CD%
call start_llama_gold.bat
pause
