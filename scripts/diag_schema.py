import lancedb
from pathlib import Path

db_path = Path(".agentx/lancedb")
if not db_path.exists():
    print("No LanceDB found.")
    exit()

db = lancedb.connect(str(db_path))
tables = db.list_tables()
if hasattr(tables, "tables"):
    table_names = tables.tables
else:
    table_names = tables

print(f"Checking {len(table_names)} tables in {db_path}...")

for name in table_names:
    try:
        t = db.open_table(name)
        schema = t.schema
        vector_field = [f for f in schema if f.name == "vector"]
        if vector_field:
            v_type = vector_field[0].type
            # Extract dimension from FixedSizeListType
            try:
                # v_type is usually FixedSizeListType(float32, 1536)
                dim = v_type.list_size
                print(f"Table '{name}': Vector dimension = {dim}")
                if dim != 384:
                    print(f"  [MISMATCH] Expected 384, found {dim}")
            except AttributeError:
                print(f"Table '{name}': Vector type = {v_type} (Unknown dimension)")
        else:
            print(f"Table '{name}': No 'vector' field.")
    except Exception as e:
        print(f"Table '{name}': Error reading schema: {e}")
