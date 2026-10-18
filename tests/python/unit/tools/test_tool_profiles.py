"""
test_tool_profiles.py - Unit tests for ToolProfile and NativeToolRegistry profile-based filtering.
"""

import os
import json
from unittest.mock import patch
from aja.orchestration.tools.native import (
    NativeToolRegistry,
    ToolProfile,
    CODE_TOOL_NAMES,
    BROWSER_TOOL_NAMES,
    DESKTOP_TOOL_NAMES,
    MOBILE_TOOL_NAMES,
)


def test_tool_profile_code_filters_correctly():
    """Verify profile='code' returns strictly the essential coding tools."""
    registry = NativeToolRegistry()
    schemas = registry.get_schemas(profile=ToolProfile.CODE)
    tool_names = {s["function"]["name"] for s in schemas}

    # All returned tools must be in CODE_TOOL_NAMES
    assert tool_names.issubset(CODE_TOOL_NAMES)
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "run_shell_command" in tool_names
    assert "grep_search" in tool_names
    assert "multi_replace" in tool_names

    # Mobile, desktop, and browser tools must NOT be present
    assert "mobile_send_sms" not in tool_names
    assert "desktop__screenshot" not in tool_names
    assert "browser__navigate" not in tool_names
    assert "manage_local_models" not in tool_names
    assert "send_telegram_message" not in tool_names


def test_tool_profile_browser_includes_code_and_browser():
    """Verify profile='browser' returns code tools plus browser tools."""
    registry = NativeToolRegistry()
    schemas = registry.get_schemas(profile=ToolProfile.BROWSER)
    tool_names = {s["function"]["name"] for s in schemas}

    assert "read_file" in tool_names
    assert "browser__navigate" in tool_names
    assert "browser__click" in tool_names
    assert "mobile_send_sms" not in tool_names
    assert "desktop__screenshot" not in tool_names


def test_tool_profile_all_returns_complete_inventory():
    """Verify profile='all' or None returns the full tool inventory for backwards compatibility."""
    registry = NativeToolRegistry()
    all_schemas = registry.get_schemas(profile=ToolProfile.ALL)
    default_schemas = registry.get_schemas()

    assert len(all_schemas) >= 40
    assert len(all_schemas) == len(default_schemas)

    all_names = {s["function"]["name"] for s in all_schemas}
    assert "read_file" in all_names
    assert "mobile_send_sms" in all_names
    assert "desktop__screenshot" in all_names
    assert "browser__navigate" in all_names


def test_tool_profile_env_var_override():
    """Verify AJA_TOOL_PROFILE environment variable sets profile when none passed."""
    registry = NativeToolRegistry()
    with patch.dict(os.environ, {"AJA_TOOL_PROFILE": "code"}):
        schemas = registry.get_schemas()
        tool_names = {s["function"]["name"] for s in schemas}
        assert tool_names.issubset(CODE_TOOL_NAMES)
        assert "mobile_send_sms" not in tool_names


def test_tool_profile_token_diet_reduction():
    """Verify that 'code' profile significantly reduces schema character/token size (~70% reduction)."""
    registry = NativeToolRegistry()
    full_schemas = registry.get_schemas(profile="all")
    code_schemas = registry.get_schemas(profile="code")

    full_chars = len(json.dumps(full_schemas))
    code_chars = len(json.dumps(code_schemas))

    # Code profile should be roughly 1/3 the size of the full suite
    assert code_chars < (full_chars * 0.40)
    assert len(code_schemas) <= 14
    assert len(full_schemas) >= 40


def test_tool_profile_unknown_fallback():
    """Verify unknown profile logs warning and safely falls back to CODE profile."""
    registry = NativeToolRegistry()
    schemas = registry.get_schemas(profile="non_existent_profile_xyz")
    tool_names = {s["function"]["name"] for s in schemas}
    assert tool_names.issubset(CODE_TOOL_NAMES)
