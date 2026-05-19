import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OPENBLAS_MAIN_FREE"] = "1"
import lancedb
print("Connecting to lancedb...")
db_path = "./.agentx/lancedb"
if not os.path.exists(db_path):
    print(f"Path {db_path} does not exist!")
else:
    db = lancedb.connect(db_path)
    print("Connected successfully.")
    print(f"Tables: {db.list_tables()}")
