import re
from typing import List, Dict, Any, Optional
from agentx.runtime.memory import MemoryTree

class TokenJuice:
    """
    Intelligent token compression for terminal logs.
    Uses optimized summary patterns for efficient context management.
    """
    def __init__(self, memory: Optional[MemoryTree] = None):
        self.memory = memory
        self.rules = {
            "npm": r"npm install.*|added \d+ packages.*|found \d+ vulnerabilities.*",
            "pip": r"Collecting.*|Downloading.*|Installing collected packages.*",
            "cargo": r"Compiling.*|Finished.*|Updating.*",
            "git": r"Enumerating objects.*|Counting objects.*|Delta compression.*"
        }

    def squeeze(self, raw_output: str) -> str:
        """
        Compresses verbose output into a surgical summary.
        If a MemoryTree is provided, it stores the full log and returns a reference.
        """
        if not raw_output:
            return ""

        lines = raw_output.splitlines()
        if len(lines) < 20:
            return raw_output

        # Summarization logic
        summary = []
        for line in lines:
            matched = False
            for tool, pattern in self.rules.items():
                if re.search(pattern, line, re.IGNORECASE):
                    summary.append(f"[{tool.upper()} OUTPUT OMITTED]")
                    matched = True
                    break
            if not matched:
                summary.append(line)

        # Deduplicate consecutive omitted markers
        final_summary = []
        last_line = None
        for line in summary:
            if line == last_line and "OMITTED" in line:
                continue
            final_summary.append(line)
            last_line = line

        condensed = "\n".join(final_summary)
        
        # Store the full context in the Memory Tree for large logs
        if self.memory and len(raw_output) > 1000:
            self.memory.add_activity(raw_output, {"type": "raw_log", "size": len(raw_output)})
            condensed += f"\n[Full log stored in Memory Tree for reference]"

        return condensed
