@echo off
set PYTHONPATH=libs\agentx-core
set GEMINI_API_KEY=AIzaSyD5i1WDARmil2BAa9XAkCgW5uzztpohCcg
set GOOGLE_API_KEY=AIzaSyD5i1WDARmil2BAa9XAkCgW5uzztpohCcg
set OPENBLAS_NUM_THREADS=1
set OPENBLAS_MAIN_FREE=1
set KMP_DUPLICATE_LIB_OK=TRUE
set OMP_NUM_THREADS=1
set MKL_NUM_THREADS=1
set PYTHONUNBUFFERED=1
set PYTHONIOENCODING=utf-8

echo [DEBUG] Starting Autonomous Worker only...
python -m agentx.runtime.autonomous_loop
