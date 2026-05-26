@echo off
REM =======================================================
REM AJA Debug Worker Launcher
REM Starts the autonomous worker only (no gateway).
REM
REM Usage:
REM   1. Copy .env.example to .env and fill in your keys
REM   2. Run this script from the project root
REM =======================================================

REM Load secrets from .env file (do NOT hardcode keys here)
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

set PYTHONPATH=libs\aja-core
set OPENBLAS_NUM_THREADS=1
set OPENBLAS_MAIN_FREE=1
set KMP_DUPLICATE_LIB_OK=TRUE
set OMP_NUM_THREADS=1
set MKL_NUM_THREADS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

echo [DEBUG] Starting Autonomous Worker only...
python -m aja.runtime.autonomous_loop
