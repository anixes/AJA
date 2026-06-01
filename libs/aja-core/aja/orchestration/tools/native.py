import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import traceback

class NativeToolRegistry:
    def __init__(self):
        self.tools = {}
        self.register_default_tools()

    def register_default_tools(self):
        self.tools["read_file"] = self.read_file
        self.tools["write_file"] = self.write_file
        self.tools["grep_search"] = self.grep_search
        self.tools["multi_replace"] = self.multi_replace

    def get_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read the contents of a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file."}
                        },
                        "required": ["path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create or overwrite a file with specific content.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file."},
                            "content": {"type": "string", "description": "The full content to write."}
                        },
                        "required": ["path", "content"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "grep_search",
                    "description": "Search for a regex pattern inside a specific directory or file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "The search pattern (string or regex)."},
                            "path": {"type": "string", "description": "Absolute path to the directory or file to search in."}
                        },
                        "required": ["query", "path"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "multi_replace",
                    "description": "Replace multiple specific exact text blocks in a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Absolute path to the file."},
                            "replacements": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "target": {"type": "string", "description": "Exact existing text block to replace."},
                                        "replacement": {"type": "string", "description": "New text to insert."}
                                    },
                                    "required": ["target", "replacement"]
                                }
                            }
                        },
                        "required": ["path", "replacements"]
                    }
                }
            }
        ]

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        try:
            return self.tools[name](**arguments)
        except Exception as e:
            return f"Tool Execution Error: {str(e)}\n{traceback.format_exc()}"

    def read_file(self, path: str) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"Error: File {path} does not exist."
            if p.is_dir():
                return f"Error: {path} is a directory, not a file."
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"Error reading file: {e}"

    def write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Successfully wrote to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    def grep_search(self, query: str, path: str) -> str:
        import subprocess
        try:
            # We use ripgrep or standard grep for fast searching. If not available, fallback to python.
            # Using python for safety and portability:
            p = Path(path)
            if not p.exists():
                return f"Error: Path {path} does not exist."
            
            results = []
            pattern = re.compile(query, re.IGNORECASE)
            
            def search_file(file_path):
                try:
                    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    for i, line in enumerate(lines):
                        if pattern.search(line):
                            results.append(f"{file_path}:{i+1}:{line.strip()}")
                except Exception:
                    pass

            if p.is_file():
                search_file(p)
            else:
                for f in p.rglob("*"):
                    if f.is_file() and not any(part.startswith('.') for part in f.parts):
                        search_file(f)
                        if len(results) > 200: # limit results
                            results.append("... [Search truncated due to too many results]")
                            break
            
            if not results:
                return "No matches found."
            return "\n".join(results)
        except Exception as e:
            return f"Error during search: {e}"

    def multi_replace(self, path: str, replacements: List[Dict[str, str]]) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"Error: File {path} does not exist."
            
            content = p.read_text(encoding="utf-8")
            for rep in replacements:
                target = rep.get("target", "")
                replacement = rep.get("replacement", "")
                if target not in content:
                    return f"Error: Exact target text '{target}' not found in {path}."
                content = content.replace(target, replacement, 1)
            
            p.write_text(content, encoding="utf-8")
            return f"Successfully applied replacements to {path}"
        except Exception as e:
            return f"Error replacing text: {e}"
