"""
agentx/skills/skill_introspect.py
==================================
Phase 9 — Gap 4: Skill explainability and management interface.

Hermes supports viewing, editing, and invoking skills directly.
This module provides the explainability layer, formatting skills into
human-readable summaries, diffs, and validation reports.

Public API
----------
  explain_skill(skill_id) -> str
  compare_versions(skill_family_id, v1: int, v2: int) -> str
  list_prerequisites() -> dict
  format_ambiguity_prompt(query: str, skills: list) -> str
"""

import json
from agentx.skills.skill_store import get_skill, get_skill_sources, _get_conn


def explain_skill(skill_id: str) -> str:
    """
    Return a human-readable markdown explanation of a skill.
    Includes tools used, parameters, postconditions, and confidence metrics.
    """
    skill = get_skill(skill_id)
    if not skill:
        return f"[!] Skill not found: {skill_id}"

    out = []
    out.append(f"# Skill: {skill.get('name', skill_id)}")
    out.append(f"**ID:** {skill_id[:12]} | **Family:** {skill.get('family_id', 'unknown')[:12]} | **Version:** {skill.get('version', 1)}")
    out.append(f"**Risk Level:** {skill.get('risk_level', 'LOW')} | **Confidence:** {skill.get('confidence_score', 0):.2f}")
    out.append(f"**Success/Failure:** {skill.get('success_count', 0)} / {skill.get('failure_count', 0)}")
    
    status = "STALE" if skill.get("is_stale") else "ACTIVE"
    out.append(f"**Status:** {status} (Last used: {skill.get('last_used_at', 'Never')[:10]})")
    out.append("")
    
    out.append("## Description")
    out.append(skill.get("description", "No description provided."))
    out.append(f"**Trigger Pattern:** `{skill.get('input_pattern', '')}`")
    out.append(f"**Tags:** {skill.get('tags', '[]')}")
    out.append("")

    out.append("## Prerequisites")
    try:
        prereqs = json.loads(skill.get("prerequisites", "[]"))
        if prereqs:
            for p in prereqs:
                out.append(f"- {p}")
        else:
            out.append("- None")
    except Exception:
        out.append("- None")
    out.append("")

    out.append("## Tool Sequence")
    try:
        seq = json.loads(skill.get("tool_sequence", "[]"))
        for i, step in enumerate(seq):
            out.append(f"  {i+1}. **{step.get('tool_name')}**")
            args = step.get("args_schema", {})
            if args:
                out.append(f"     Params: {list(args.keys())}")
    except Exception:
        out.append("  [Invalid tool sequence data]")
    out.append("")

    out.append("## Correctness Assertions (Postconditions)")
    try:
        pcs = json.loads(skill.get("postconditions", "[]"))
        if pcs:
            for pc in pcs:
                req = "(Required)" if pc.get("required", True) else "(Warning Only)"
                out.append(f"- `[{pc.get('type')}]` {pc.get('target')} ⟶ {pc.get('expected')} {req}")
        else:
            out.append("- None defined.")
    except Exception:
        out.append("- None defined.")

    return "\n".join(out)


def compare_versions(family_id: str, v1: int, v2: int) -> str:
    """Compare tool sequences between two versions of the same skill family."""
    try:
        conn = _get_conn()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT version, tool_sequence FROM skills WHERE family_id = ? AND version IN (?, ?)",
            (family_id, v1, v2)
        ).fetchall()
        conn.close()

        if len(rows) < 2:
            return "[!] Could not find both versions for comparison."

        skills = {r["version"]: r for r in rows}
        seq1 = json.loads(skills[v1]["tool_sequence"])
        seq2 = json.loads(skills[v2]["tool_sequence"])

        tools1 = [s.get("tool_name") for s in seq1]
        tools2 = [s.get("tool_name") for s in seq2]

        out = [f"### Diff: v{v1} ⟶ v{v2}"]
        out.append(f"- **v{v1}:** {' ⟶ '.join(tools1)}")
        out.append(f"- **v{v2}:** {' ⟶ '.join(tools2)}")
        return "\n".join(out)
    except Exception as e:
        import traceback
        return f"[!] Diff error: {e}"


def format_ambiguity_prompt(query: str, skills: list) -> str:
    """Format an interactive prompt for Gap 3 ambiguity resolution."""
    out = [f"\n[?] Ambiguity detected for query: '{query}'"]
    out.append("Multiple skills match with very similar scores. Please select one:\n")
    
    for i, s in enumerate(skills, 1):
        out.append(f"  {i}) {s.get('name', 'Unnamed')} (Confidence: {s.get('confidence_score', 0):.2f})")
        out.append(f"     Pattern: {s.get('input_pattern', '')}")
        
        try:
            seq = json.loads(s.get("tool_sequence", "[]"))
            tools = [step.get("tool_name") for step in seq]
            out.append(f"     Tools:   {' ⟶ '.join(tools)}")
        except:
            pass
        out.append("")
    
    out.append(f"  0) None of the above (fallback to normal execution)\n")
    return "\n".join(out)

