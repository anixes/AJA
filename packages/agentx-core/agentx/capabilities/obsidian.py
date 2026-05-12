import os
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from .base import Capability, CapabilityResult

class ObsidianCapability(Capability):
    """
    Capability to interact with a local Obsidian Vault for long-term knowledge storage.
    Supports YAML frontmatter and structured mission archiving.
    """
    def __init__(self, vault_path: Optional[str] = None):
        super().__init__()
        self.name = "obsidian"
        self.description = "Read, write, and search notes in a local Obsidian Vault with graph metadata."
        self.vault_path = vault_path or os.environ.get("OBSIDIAN_VAULT_PATH")

    def _get_note_path(self, note_name: str) -> Path:
        if not self.vault_path:
            raise ValueError("OBSIDIAN_VAULT_PATH not set.")
        # Ensure name doesn't have .md twice
        clean_name = note_name.replace(".md", "")
        return Path(self.vault_path) / f"{clean_name}.md"

    def _apply_template(self, content: str, tags: List[str] = None) -> str:
        """Adds YAML frontmatter for Obsidian Graph View."""
        default_tags = ["agentx", "long-term-memory"]
        if tags:
            default_tags.extend(tags)
        
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = "---\n"
        header += f"created: {now}\n"
        header += f"tags: [{', '.join(default_tags)}]\n"
        header += "source: agentx-orchestrator\n"
        header += "---\n\n"
        return header + content

    async def execute(self, action: str, **kwargs) -> CapabilityResult:
        if not self.vault_path:
            return CapabilityResult(success=False, output="Error: Obsidian Vault path not configured. Set OBSIDIAN_VAULT_PATH.")

        try:
            if action == "write_note":
                name = kwargs.get("name")
                content = kwargs.get("content", "")
                tags = kwargs.get("tags", [])
                
                formatted_content = self._apply_template(content, tags)
                path = self._get_note_path(name)
                path.parent.mkdir(parents=True, exist_ok=True)
                
                with open(path, "w", encoding="utf-8") as f:
                    f.write(formatted_content)
                return CapabilityResult(success=True, output=f"AgentX: Note '{name}' archived to vault.")

            elif action == "append_to_note":
                name = kwargs.get("name")
                content = kwargs.get("content", "")
                path = self._get_note_path(name)
                
                if not path.exists():
                    # Create with template if new
                    return await self.execute("write_note", name=name, content=content)
                
                with open(path, "a", encoding="utf-8") as f:
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    f.write(f"\n\n### [{timestamp}] Log Entry\n{content}")
                return CapabilityResult(success=True, output=f"AgentX: Appended entry to '{name}'.")

            elif action == "list_notes":
                notes = list(Path(self.vault_path).rglob("*.md"))
                note_names = [n.stem for n in notes]
                return CapabilityResult(success=True, output=f"Found {len(note_names)} notes in vault.")

            elif action == "read_note":
                name = kwargs.get("name")
                path = self._get_note_path(name)
                if not path.exists():
                    return CapabilityResult(success=False, output=f"Note '{name}' not found.")
                with open(path, "r", encoding="utf-8") as f:
                    return CapabilityResult(success=True, output=f.read())

            return CapabilityResult(success=False, output=f"Unknown action: {action}")
        except Exception as e:
            return CapabilityResult(success=False, output=f"Obsidian Error: {str(e)}")

    def get_description(self) -> str:
        return "Manage long-term human-readable knowledge in Obsidian."
