"""
=============================================================================
Unit Test: Copilot Token In-Memory Memoization & Invalidation
=============================================================================
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from aja.copilot_auth import (
    resolve_copilot_token,
    invalidate_copilot_cache,
)


def test_resolve_copilot_token_in_memory_caching():
    invalidate_copilot_cache()
    # Temporarily remove env vars so it goes to CLI fallback
    old_env = {}
    for k in ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"]:
        if k in os.environ:
            old_env[k] = os.environ.pop(k)

    try:
        with patch("aja.copilot_auth._try_gh_cli_token") as mock_gh:
            mock_gh.return_value = "gho_testtoken123456789"

            # First call: triggers _try_gh_cli_token
            token1, src1 = resolve_copilot_token()
            assert token1 == "gho_testtoken123456789"
            assert src1 == "gh auth token"
            assert mock_gh.call_count == 1

            # Second call: must return from in-memory cache without calling _try_gh_cli_token again
            token2, src2 = resolve_copilot_token()
            assert token2 == "gho_testtoken123456789"
            assert src2 == "gh auth token"
            assert mock_gh.call_count == 1  # Still 1! Zero subprocess calls!
    finally:
        os.environ.update(old_env)


def test_invalidate_copilot_cache_resets_token():
    invalidate_copilot_cache()
    old_env = {}
    for k in ["COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"]:
        if k in os.environ:
            old_env[k] = os.environ.pop(k)

    try:
        with patch("aja.copilot_auth._try_gh_cli_token") as mock_gh:
            mock_gh.return_value = "gho_initialtoken"
            resolve_copilot_token()
            assert mock_gh.call_count == 1

            # Invalidate cache (simulating HTTP 401/403)
            invalidate_copilot_cache()

            mock_gh.return_value = "gho_refreshedtoken"
            token, _ = resolve_copilot_token()
            assert token == "gho_refreshedtoken"
            assert mock_gh.call_count == 2
    finally:
        os.environ.update(old_env)
