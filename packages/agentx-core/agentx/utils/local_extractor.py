import os
import ast
import re
import json
from pathlib import Path

class LocalExtractor:
    """
    Extracts structural nodes (classes, functions) without using AI tokens.
    """
    def __init__(self):
        self.nodes = []
        self.edges = []
        self.seen_ids = set()

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
        except:
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
        # 1. Scan Files
        for root, _, files in os.walk("."):
            if "node_modules" in root or ".git" in root or "graphify-out" in root:
                continue
            for f in files:
                path = Path(root) / f
                if path.suffix == ".py":
                    self.extract_python(path)
                elif path.suffix == ".ts":
                    self.extract_ts(path)

        # 2. Merge with existing semantic data if possible
        # For this demo, we just overwrite the local structure
        output = {
            "nodes": self.nodes,
            "edges": self.edges,
            "metadata": {"type": "local_ast_sync"}
        }
        
        out_dir = Path("graphify-out")
        out_dir.mkdir(exist_ok=True)
        (out_dir / "graph_local.json").write_text(json.dumps(output, indent=2))
        
        # 3. Simple HTML update (Optional/Simplified)
        # We'll just print status for now
        print(f"Mapped {len(self.nodes)} nodes and {len(self.edges)} edges.")

if __name__ == "__main__":
    LocalExtractor().run()
