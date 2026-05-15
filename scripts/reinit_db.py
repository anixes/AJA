import lancedb
from pathlib import Path

# New path after reorganization
db_path = Path(".agentx/lancedb")
if not db_path.exists():
    print("No LanceDB found.")
    exit()

db = lancedb.connect(str(db_path))
mismatched_tables = ["aja_skills", "core_plans", "decision_logs", "mission_semantic"]

print(f"Cleaning up {len(mismatched_tables)} mismatched tables in {db_path}...")

for name in mismatched_tables:
    try:
        db.drop_table(name)
        print(f"Table '{name}': Dropped.")
    except Exception as e:
        print(f"Table '{name}': Error dropping or already gone: {e}")

print("Database ready for re-initialization with 384D schema.")
