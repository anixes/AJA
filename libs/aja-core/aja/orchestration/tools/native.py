import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
import traceback

class NativeToolRegistry:
    _external_schemas: Dict[str, Dict[str, Any]] = {}

    def __init__(self):
        self.tools = {}
        self.register_default_tools()

    @classmethod
    def register_external_schema(cls, schema: Dict[str, Any]) -> None:
        fn = schema.get("function", schema)
        name = fn.get("name")
        if name:
            cls._external_schemas[name] = {"type": "function", "function": fn}

    @classmethod
    def clear_external_schemas(cls, prefix: Optional[str] = None) -> None:
        if prefix is None:
            cls._external_schemas.clear()
            return
        for name in [name for name in cls._external_schemas if name.startswith(prefix)]:
            cls._external_schemas.pop(name, None)

    @classmethod
    def register_mcp_tools(cls, manager: Any) -> None:
        for schema in manager.get_registry_schemas():
            cls.register_external_schema(schema)

    def register_default_tools(self):
        self.tools["read_file"] = self.read_file
        self.tools["write_file"] = self.write_file
        self.tools["grep_search"] = self.grep_search
        self.tools["multi_replace"] = self.multi_replace
        self.tools["sleep"] = self.sleep
        self.tools["run_shell_command"] = self.run_shell_command

    def get_schemas(self) -> List[Dict[str, Any]]:
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "activity_type": "python",
                    "retry_policy": "safe",
                    "required_scope": "python.read_file",
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
                    "activity_type": "python",
                    "retry_policy": "safe",
                    "required_scope": "python.write_file",
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
                    "activity_type": "python",
                    "retry_policy": "safe",
                    "required_scope": "python.grep_search",
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
                    "activity_type": "python",
                    "retry_policy": "safe",
                    "required_scope": "python.multi_replace",
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
            },
            {
                "type": "function",
                "function": {
                    "name": "sleep",
                    "activity_type": "python",
                    "retry_policy": "safe",
                    "required_scope": "python.sleep",
                    "description": "Pause execution for a specified number of seconds. Use this when you need to wait for a background process, server, or test to finish.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "duration_seconds": {"type": "integer", "description": "Number of seconds to wait."}
                        },
                        "required": ["duration_seconds"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "run_shell_command",
                    "activity_type": "shell",
                    "retry_policy": "none",
                    "required_scope": "shell.write",
                    "description": "Run a shell command safely via the sandboxed PTY supervisor. The working directory persists across calls within a session.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "cmd": {
                                "type": "string",
                                "description": "The exact shell command to run. Do NOT chain multiple commands with && — call run_shell_command once per command."
                            }
                        },
                        "required": ["cmd"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.navigate",
                    "activity_type": "browser",
                    "retry_policy": "none",
                    "required_scope": "browser.navigate",
                    "description": "Navigate the mission browser session to a URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}, "wait_until": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.click",
                    "activity_type": "browser",
                    "retry_policy": "none",
                    "required_scope": "browser.interact",
                    "description": "Click a selector in the mission browser session.",
                    "parameters": {
                        "type": "object",
                        "properties": {"selector": {"type": "string"}},
                        "required": ["selector"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.fill",
                    "activity_type": "browser",
                    "retry_policy": "none",
                    "required_scope": "browser.interact",
                    "description": "Fill a selector with text in the mission browser session.",
                    "parameters": {
                        "type": "object",
                        "properties": {"selector": {"type": "string"}, "text": {"type": "string"}},
                        "required": ["selector", "text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.screenshot",
                    "activity_type": "browser",
                    "retry_policy": "safe",
                    "required_scope": "browser.read",
                    "description": "Capture a screenshot of the mission browser session.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "full_page": {"type": "boolean"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.extract_text",
                    "activity_type": "browser",
                    "retry_policy": "safe",
                    "required_scope": "browser.read",
                    "description": "Extract text from a selector in the mission browser session.",
                    "parameters": {
                        "type": "object",
                        "properties": {"selector": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "browser.close",
                    "activity_type": "browser",
                    "retry_policy": "none",
                    "required_scope": "browser.interact",
                    "description": "Close the mission browser session.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "desktop.screenshot",
                    "activity_type": "desktop",
                    "retry_policy": "safe",
                    "required_scope": "desktop.interact",
                    "description": "Capture a desktop screenshot.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "desktop.click",
                    "activity_type": "desktop",
                    "retry_policy": "none",
                    "required_scope": "desktop.interact",
                    "description": "Click a desktop coordinate.",
                    "parameters": {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string"}},
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "desktop.type",
                    "activity_type": "desktop",
                    "retry_policy": "none",
                    "required_scope": "desktop.interact",
                    "description": "Type text into the active desktop target.",
                    "parameters": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}, "interval": {"type": "number"}},
                        "required": ["text"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "desktop.hotkey",
                    "activity_type": "desktop",
                    "retry_policy": "none",
                    "required_scope": "desktop.interact",
                    "description": "Press a desktop hotkey sequence.",
                    "parameters": {
                        "type": "object",
                        "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                        "required": ["keys"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "desktop.move_mouse",
                    "activity_type": "desktop",
                    "retry_policy": "none",
                    "required_scope": "desktop.interact",
                    "description": "Move the desktop mouse pointer.",
                    "parameters": {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "duration": {"type": "number"}},
                    },
                },
            },
        ]
        try:
            from aja.api.mcp_client import get_default_mcp_manager
            for schema in get_default_mcp_manager().get_registry_schemas():
                self.register_external_schema(schema)
        except Exception:
            pass
        return schemas + list(self._external_schemas.values())

    def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        if name not in self.tools:
            return f"Error: Tool '{name}' not found."
        try:
            return self.tools[name](**arguments)
        except Exception as e:
            return f"Tool Execution Error: {str(e)}\n{traceback.format_exc()}"

    def dispatch(self, name: str, arguments: Dict[str, Any], trace_id: str) -> Any:
        from aja.orchestration.activity_rt import Activity, ActivityType, RetryPolicy
        schema = next((t["function"] for t in self.get_schemas() if t["function"]["name"] == name), None)
        if not schema:
            raise ValueError(f"Tool '{name}' not registered")
        activity_type = schema.get("activity_type", "python")
        retry_policy = schema.get("retry_policy", "none")
        return Activity(
            tool=name,
            args=arguments,
            activity_type=ActivityType(activity_type),
            retry_policy=RetryPolicy(retry_policy),
            trace_id=trace_id,
            metadata={
                "required_scope": schema.get("required_scope"),
                "schema_name": name,
                **dict(schema.get("metadata", {})),
            },
        )

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

    def sleep(self, duration_seconds: int) -> str:
        try:
            import time
            duration = int(duration_seconds)
            time.sleep(duration)
            return f"Successfully paused execution for {duration} seconds."
        except Exception as e:
            return f"Error during sleep: {e}"

    def run_shell_command(self, cmd: str) -> str:
        import subprocess
        try:
            res = subprocess.run(cmd, shell=True, text=True, capture_output=True, timeout=120)
            if res.returncode == 0:
                return res.stdout
            return f"Error: Command failed with state {res.returncode}\nStdout: {res.stdout}\nStderr: {res.stderr}"
        except Exception as e:
            return f"Error executing shell command: {e}"
