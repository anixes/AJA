import os
import sys
import psutil
import lancedb
import pyarrow as pa
import agentx_native

def diag():
    print(f"Python: {sys.version}")
    print(f"Platform: {sys.platform}")
    print(f"Current PID: {os.getpid()}")
    
    # Environment variables
    vars_to_check = [
        "OPENBLAS_NUM_THREADS", 
        "OPENBLAS_MAIN_FREE", 
        "KMP_DUPLICATE_LIB_OK", 
        "PYTHONPATH"
    ]
    print("\n--- Environment Variables ---")
    for v in vars_to_check:
        print(f"{v}: {os.getenv(v)}")
        
    # Memory usage
    process = psutil.Process(os.getpid())
    mem = process.memory_info().rss / (1024 * 1024)
    print(f"\n--- Memory Usage ---")
    print(f"RSS: {mem:.2f} MB")
    
    # Check agentx_native
    print(f"\n--- agentx_native ---")
    print(f"Version: {agentx_native.version()}")
    print(f"File: {agentx_native.__file__}")
    
    # Check LanceDB
    print(f"\n--- LanceDB ---")
    try:
        db = lancedb.connect("./.agentx/memory.lancedb")
        print(f"Connected to LanceDB. Tables: {db.table_names()}")
    except Exception as e:
        print(f"LanceDB Error: {e}")

if __name__ == "__main__":
    diag()
