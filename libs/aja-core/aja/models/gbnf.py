"""
aja.models.gbnf — GGML BNF (GBNF) Grammars and Structured Output Schemas.
========================================================================
Enables deterministic, schema-constrained tool calling in llama.cpp to eliminate
JSON formatting errors, missing required parameters, and hallucinated tool names.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple


def tools_to_json_schema(tools: List[Dict[str, Any]], allow_text_reply: bool = True) -> Dict[str, Any]:
    """
    Compile a list of OpenAI-formatted tool definitions into a unified JSON schema
    compatible with llama-server's `-j` / `--json-schema` or `response_format`.

    Each tool's parameter schema is preserved under an `anyOf` branch, ensuring
    strict validation of tool-specific required fields.
    """
    if not tools:
        return {"type": "object"}

    tool_branches: List[Dict[str, Any]] = []

    for t in tools:
        func = t.get("function", t)
        fname = func.get("name", "")
        params = func.get("parameters", {"type": "object", "properties": {}})

        tool_branch = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "const": fname},
                "arguments": params,
            },
            "required": ["name", "arguments"],
        }
        tool_branches.append(tool_branch)

    if allow_text_reply:
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "thought": {
                    "type": "string",
                    "description": "Optional reasoning steps before acting",
                },
                "action": {
                    "type": "string",
                    "enum": ["tool_call", "message"],
                },
                "message": {
                    "type": "string",
                    "description": "Direct reply to the user when action is 'message'",
                },
                "tool_call": {
                    "type": "object",
                    "anyOf": tool_branches,
                },
            },
            "required": ["action"],
        }
    else:
        schema = {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "tool_call": {
                    "type": "object",
                    "anyOf": tool_branches,
                },
            },
            "required": ["tool_call"],
        }

    return schema


def build_tool_call_grammar(tools: List[Dict[str, Any]], allow_narrative: bool = True) -> str:
    """
    Generate a complete, syntactically valid GBNF grammar string that constrains
    llama.cpp generations to valid JSON tool calls.
    """
    if not tools:
        return "root ::= [^\\x00]+"

    tool_names: List[str] = []
    for t in tools:
        func = t.get("function", t)
        name = func.get("name")
        if name:
            tool_names.append(f'"{name}"')

    if not tool_names:
        return "root ::= [^\\x00]+"

    func_names_rule = " | ".join(tool_names)

    grammar_lines = [
        "root ::= " + ("tool-call | text-message" if allow_narrative else "tool-call"),
        'tool-call ::= "```json\\n" ws json-tool ws "```" | json-tool',
        'json-tool ::= "{" ws "\\"name\\":" ws func-name "," ws "\\"arguments\\":" ws json-object ws "}"',
        f"func-name ::= {func_names_rule}",
        "text-message ::= [^\\x00]+",
        "ws ::= [ \\t\\n\\r]*",
        'json-object ::= "{" ws (json-pair ("," ws json-pair)*)? ws "}"',
        'json-pair ::= string ":" ws json-value',
        'json-value ::= json-object | json-array | string | number | "true" | "false" | "null"',
        'json-array ::= "[" ws (json-value ("," ws json-value)*)? ws "]"',
        'string ::= "\\"" ([^"\\\\\\x00-\\x1f] | "\\\\" ["\\\\/bfnrt] | "\\\\u" [0-9a-fA-F]{4})* "\\""',
        'number ::= "-"? [0-9]+ ("." [0-9]+)? ([eE] [-+]? [0-9]+)?',
    ]

    return "\n".join(grammar_lines)


def parse_constrained_tool_response(content: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parse the output from a grammar-constrained or JSON-schema constrained response.
    Returns: (text_content, tool_calls_list).
    """
    if not content or not content.strip():
        return "", []

    text = content.strip()

    # If wrapped in markdown block, strip outer block
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()

    # Attempt JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # 1. Action format with tool_call
            if data.get("action") == "tool_call" and "tool_call" in data:
                tc = data["tool_call"]
                if isinstance(tc, dict) and "name" in tc:
                    args = tc.get("arguments", {})
                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                    return data.get("thought", ""), [
                        {
                            "id": f"call_{abs(hash(tc['name'])) % 1000000}",
                            "name": tc["name"],
                            "arguments": args_str,
                        }
                    ]

            # 2. Action format with direct message
            if data.get("action") == "message" and "message" in data:
                return data["message"], []

            # 3. Direct tool_call format
            if "tool_call" in data and isinstance(data["tool_call"], dict):
                tc = data["tool_call"]
                if "name" in tc:
                    args = tc.get("arguments", {})
                    args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                    return data.get("thought", ""), [
                        {
                            "id": f"call_{abs(hash(tc['name'])) % 1000000}",
                            "name": tc["name"],
                            "arguments": args_str,
                        }
                    ]

            # 4. Raw {name: ..., arguments: ...}
            if "name" in data and "arguments" in data:
                args = data.get("arguments", {})
                args_str = json.dumps(args) if isinstance(args, dict) else str(args)
                return "", [
                    {
                        "id": f"call_{abs(hash(data['name'])) % 1000000}",
                        "name": data["name"],
                        "arguments": args_str,
                    }
                ]
    except Exception:
        pass

    # Fallback to plain text
    return content, []
