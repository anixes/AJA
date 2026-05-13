import os
import ast
import re
import json
from pathlib import Path
import lancedb
import pyarrow as pa

class LocalExtractor:
    """
    Extracts structural nodes (classes, functions) without using AI tokens.
    """
    def __init__(self):
        self.nodes = []
        self.edges = []
        self.seen_ids = set()
        self.db_path = Path(".agent") / "lancedb" / "structure"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(self.db_path)
        self._ensure_table()

    def _ensure_table(self):
        schema = pa.schema([
            ("path", pa.string()),
            ("mtime", pa.float64()),
            ("nodes_json", pa.string()),
            ("edges_json", pa.string())
        ])
        existing_tables = self.db.list_tables()
        if hasattr(existing_tables, "tables"):
            existing_tables = existing_tables.tables
            
        if "file_cache" not in existing_tables:
            self.db.create_table("file_cache", schema=schema)

    def add_node(self, node_id, label, type_, line=None, path=None):
        if node_id not in self.seen_ids:
            self.nodes.append({
                "id": node_id, 
                "label": label, 
                "type": type_,
                "line": line,
                "path": str(path) if path else None
            })
            self.seen_ids.add(node_id)

    def extract_python(self, path):
        try:
            content = path.read_text(encoding='utf-8')
            tree = ast.parse(content)
            module_name = path.stem
            self.add_node(module_name, module_name, "module", path=path)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    class_id = f"{module_name}.{node.name}"
                    self.add_node(class_id, node.name, "class", line=node.lineno, path=path)
                    self.edges.append({"source": module_name, "target": class_id, "type": "contains"})
                elif isinstance(node, ast.FunctionDef):
                    func_id = f"{module_name}.{node.name}"
                    self.add_node(func_id, node.name, "function", line=node.lineno, path=path)
                    self.edges.append({"source": module_name, "target": func_id, "type": "contains"})
        except Exception:
            pass

    def extract_ts(self, path):
        content = path.read_text(encoding='utf-8')
        lines = content.splitlines()
        module_name = path.stem
        self.add_node(module_name, module_name, "module", path=path)

        # Better Regex for TS structural elements with line tracking
        for i, line in enumerate(lines):
            # Class detection
            class_match = re.search(r'class\s+(\w+)', line)
            if class_match:
                name = class_match.group(1)
                class_id = f"{module_name}.{name}"
                self.add_node(class_id, name, "class", line=i+1, path=path)
                self.edges.append({"source": module_name, "target": class_id, "type": "contains"})

            # Tool detection (Special Case)
            tool_match = re.search(r'(?:export\s+)?const\s+(\w+)\s*:\s*ToolDefinition', line)
            if tool_match:
                name = tool_match.group(1)
                func_id = f"{module_name}.{name}"
                self.add_node(func_id, name, "tool", line=i+1, path=path)
                self.edges.append({"source": module_name, "target": func_id, "type": "contains"})

            # Standard Method/Function detection
            method_match = re.search(r'^\s*(?:async\s+)?(\w+)\s*\(.*?\)\s*(?::\s*[\w<>\[\]]+)?\s*{', line)
            if method_match:
                name = method_match.group(1)
                if name not in ["if", "for", "while", "switch", "catch", "constructor"]:
                    func_id = f"{module_name}.{name}"
                    self.add_node(func_id, name, "function", line=i+1, path=path)
                    self.edges.append({"source": module_name, "target": func_id, "type": "contains"})

    def run(self):
        table = self.db.open_table("file_cache")
        
        # 1. Scan and Process
        for root, _, files in os.walk("."):
            if any(x in root for x in ["node_modules", ".git", "graphify-out", ".agent", "dist", "__pycache__"]):
                continue
            for f in files:
                path = Path(root) / f
                if path.suffix not in [".py", ".ts"]:
                    continue
                
                str_path = str(path)
                mtime = path.stat().st_mtime
                
                # Check cache
                cached = table.search().where(f"path = '{str_path}'").to_list()
                
                if cached and cached[0]["mtime"] == mtime:
                    # Load from cache
                    file_nodes = json.loads(cached[0]["nodes_json"])
                    file_edges = json.loads(cached[0]["edges_json"])
                    self.nodes.extend(file_nodes)
                    self.edges.extend(file_edges)
                    for n in file_nodes:
                        self.seen_ids.add(n["id"])
                    continue

                # Parse fresh
                # We need to isolate this file's contribution
                start_node_idx = len(self.nodes)
                start_edge_idx = len(self.edges)
                
                if path.suffix == ".py":
                    self.extract_python(path)
                elif path.suffix == ".ts":
                    self.extract_ts(path)
                
                new_file_nodes = self.nodes[start_node_idx:]
                new_file_edges = self.edges[start_edge_idx:]
                
                # Update table
                new_data = {
                    "path": str_path,
                    "mtime": mtime,
                    "nodes_json": json.dumps(new_file_nodes),
                    "edges_json": json.dumps(new_file_edges)
                }
                
                if cached:
                    table.update(where=f"path = '{str_path}'", values=new_data)
                else:
                    table.add([new_data])
        
        print(f"Mapped {len(self.nodes)} structural nodes across Agent codebase.")

if __name__ == "__main__":
    LocalExtractor().run()
