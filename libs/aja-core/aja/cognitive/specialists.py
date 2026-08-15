"""
AJA Cognitive Engine: Magentic-One Style Specialist Roles
Specialized sub-agent definitions for SysAdmin, Web Research, and Code Engineering.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseSpecialist(ABC):
    """Abstract base class for domain-specialized cognitive sub-agents."""
    name: str = "base_specialist"
    description: str = "Base Specialist"

    @abstractmethod
    def get_system_instructions(self) -> str:
        """Returns the specialized system prompt for this role."""
        pass

    @abstractmethod
    def get_available_tools(self) -> List[str]:
        """Returns the subset of tool identifiers relevant to this role."""
        pass


class SysAdminSpecialist(BaseSpecialist):
    """Specialist for host diagnostics, systemd services, container orchestration, and server troubleshooting."""
    name: str = "sysadmin_specialist"
    description: str = "System Administration & Infrastructure Diagnostics"

    def get_system_instructions(self) -> str:
        return """You are the AJA SysAdmin Specialist.
Your primary role is to diagnose, maintain, and troubleshoot server and host environments.
Guidelines:
1. Always inspect before modifying (check logs, service status, disk usage, active ports).
2. Formulate actions as precise shell or Python commands.
3. Be defensive: Never run destructive operations without verifying targets.
4. Report clear diagnostic summaries with root causes and remediation actions."""

    def get_available_tools(self) -> List[str]:
        return [
            "get_system_specs",
            "get_service_status",
            "inspect_docker_containers",
            "get_disk_usage",
            "get_active_ports",
            "codeact_executor",
            "run_shell",
            "read_file",
            "write_file",
        ]


class WebResearchSpecialist(BaseSpecialist):
    """Specialist for multi-turn web search, doc retrieval, paper reading, and synthesis."""
    name: str = "web_research_specialist"
    description: str = "Web Research & Technical Documentation Synthesis"

    def get_system_instructions(self) -> str:
        return """You are the AJA Web Research Specialist.
Your primary role is to gather information, search documentation, extract code examples, and synthesize findings.
Guidelines:
1. Search queries should be targeted and specific.
2. Extract clean markdown content from official docs and references.
3. Synthesize findings clearly with source links and actionable code snippets.
4. Fact-check documentation versions and deprecation notices."""

    def get_available_tools(self) -> List[str]:
        return [
            "search_web",
            "fetch_url",
            "codeact_executor",
            "read_file",
            "write_file",
        ]


class CodeEngineerSpecialist(BaseSpecialist):
    """Specialist for repository AST code graphs, file modifications, git branches, and automated TDD loops."""
    name: str = "code_engineer_specialist"
    description: str = "Software Engineering & Test-Driven Development"

    def get_system_instructions(self) -> str:
        return """You are the AJA Code Engineering Specialist.
Your primary role is to develop, refactor, and verify codebases.
Guidelines:
1. Follow Test-Driven Development (TDD): inspect existing tests before editing.
2. Make minimal, surgical edits to maintain clean git diffs.
3. Verify changes by executing unit tests.
4. Follow project conventions and preserve docstrings."""

    def get_available_tools(self) -> List[str]:
        return [
            "read_file",
            "write_file",
            "apply_patch",
            "codeact_executor",
            "run_shell",
            "git_tools",
        ]
