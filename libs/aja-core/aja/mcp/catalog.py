import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from aja.config import PROJECT_ROOT, DATA_DIR

MCP_CATALOG = {
    "sqlite": {
        "description": "SQLite database access server",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sqlite"]
    },
    "postgres": {
        "description": "Postgres database access server",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"]
    },
    "github": {
        "description": "GitHub API integration server",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"]
    },
    "gdrive": {
        "description": "Google Drive file access server",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-gdrive"]
    },
    "memory": {
        "description": "Memory store server",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"]
    }
}

def get_catalog() -> Dict[str, Dict[str, Any]]:
    return MCP_CATALOG

def check_dependencies(command: str) -> bool:
    if command == "npx" or command == "node":
        return shutil.which("node") is not None
    if command == "pip" or command == "python":
        return shutil.which("python") is not None or shutil.which("python3") is not None
    return shutil.which(command) is not None

def install_mcp_server(server_name: str) -> bool:
    server_name = server_name.lower()
    if server_name not in MCP_CATALOG:
        raise ValueError(f"MCP server '{server_name}' not found in catalog.")

    config = MCP_CATALOG[server_name]
    cmd = config["command"]
    
    if not check_dependencies(cmd):
        dep = "Node.js (node/npm/npx)" if cmd == "npx" else cmd
        raise RuntimeError(f"Missing dependency for installing '{server_name}': {dep}")

    # Determine config file path
    project_config = PROJECT_ROOT / "aja.json"
    data_config = DATA_DIR / "aja.json"
    config_path = project_config if project_config.exists() else data_config

    # Load existing config
    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    # Ensure mcp_servers is a list
    if "mcp_servers" not in data or not isinstance(data["mcp_servers"], list):
        data["mcp_servers"] = []

    # Check if already installed
    existing = next((s for s in data["mcp_servers"] if s.get("server_id") == server_name), None)
    if existing:
        existing["command"] = cmd
        existing["args"] = config["args"]
        existing["enabled"] = True
    else:
        data["mcp_servers"].append({
            "server_id": server_name,
            "transport": "stdio",
            "enabled": True,
            "command": cmd,
            "args": config["args"]
        })

    # Save configuration
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

    # If it is project root, sync it with DATA_DIR / "aja.json" too
    if config_path != DATA_DIR / "aja.json":
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "aja.json").open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    return True
