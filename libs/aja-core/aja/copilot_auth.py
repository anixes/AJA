"""GitHub Copilot authentication utilities for AJA.

Implements the OAuth device code flow and token exchange for the Copilot API.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# OAuth device code flow constants (same client ID as opencode/Copilot CLI)
COPILOT_OAUTH_CLIENT_ID = "Ov23li8tweQw6odWQebz"
_CLASSIC_PAT_PREFIX = "ghp_"
COPILOT_ENV_VARS = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
_DEVICE_CODE_POLL_INTERVAL = 5
_DEVICE_CODE_POLL_SAFETY_MARGIN = 3


def validate_copilot_token(token: str) -> tuple[bool, str]:
    """Validate that a token is usable with the Copilot API."""
    token = token.strip()
    if not token:
        return False, "Empty token"
    if token.startswith(_CLASSIC_PAT_PREFIX):
        return False, "Classic PATs (ghp_*) are not supported. Use device code or fine-grained PAT."
    return True, "OK"


def resolve_copilot_token() -> tuple[str, str]:
    """Resolve a GitHub token suitable for Copilot API use."""
    # 1. Check env vars
    for env_var in COPILOT_ENV_VARS:
        val = os.getenv(env_var, "").strip()
        if val:
            valid, msg = validate_copilot_token(val)
            if not valid:
                logger.warning("Token from %s is not supported: %s", env_var, msg)
                continue
            return val, env_var

    # 2. Try gh auth token (fallback)
    token = _try_gh_cli_token()
    if token:
        valid, msg = validate_copilot_token(token)
        if valid:
            return token, "gh auth token"

    return "", ""


def _gh_cli_candidates() -> list[str]:
    candidates = []
    resolved = shutil.which("gh")
    if resolved:
        candidates.append(resolved)
    for candidate in (
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
        str(Path.home() / ".local" / "bin" / "gh"),
    ):
        if candidate not in candidates and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            candidates.append(candidate)
    return candidates


def _try_gh_cli_token() -> Optional[str]:
    hostname = os.getenv("COPILOT_GH_HOST", "").strip()
    clean_env = {k: v for k, v in os.environ.items() if k not in {"GITHUB_TOKEN", "GH_TOKEN"}}
    for gh_path in _gh_cli_candidates():
        cmd = [gh_path, "auth", "token"]
        if hostname:
            cmd += ["--hostname", hostname]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=clean_env)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def copilot_device_code_login(host: str = "github.com", timeout_seconds: float = 300) -> Optional[str]:
    """Run the GitHub OAuth device code flow for Copilot."""
    import urllib.request
    import urllib.parse

    domain = host.rstrip("/")
    device_code_url = f"https://{domain}/login/device/code"
    access_token_url = f"https://{domain}/login/oauth/access_token"

    data = urllib.parse.urlencode({"client_id": COPILOT_OAUTH_CLIENT_ID, "scope": "read:user"}).encode()
    req = urllib.request.Request(
        device_code_url,
        data=data,
        headers={"Accept": "application/json", "User-Agent": "AJA-Gateway/1.0"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_data = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"[Copilot] Failed to start device authorization: {exc}")
        return None

    verification_uri = device_data.get("verification_uri", "https://github.com/login/device")
    user_code = device_data.get("user_code", "")
    device_code = device_data.get("device_code", "")
    interval = max(device_data.get("interval", _DEVICE_CODE_POLL_INTERVAL), 1)

    if not device_code or not user_code:
        print("[Copilot] GitHub did not return a device code.")
        return None

    print(f"\n[Copilot] Open this URL in your browser: {verification_uri}")
    print(f"[Copilot] Enter this code: {user_code}")
    print("[Copilot] Waiting for authorization...", end="", flush=True)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(interval + _DEVICE_CODE_POLL_SAFETY_MARGIN)
        poll_data = urllib.parse.urlencode({
            "client_id": COPILOT_OAUTH_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }).encode()
        poll_req = urllib.request.Request(
            access_token_url,
            data=poll_data,
            headers={"Accept": "application/json", "User-Agent": "AJA-Gateway/1.0"},
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception:
            print(".", end="", flush=True)
            continue

        if result.get("access_token"):
            print(" ✓\n")
            return result["access_token"]

        error = result.get("error", "")
        if error == "authorization_pending":
            print(".", end="", flush=True)
            continue
        elif error == "slow_down":
            interval += 5
            print(".", end="", flush=True)
            continue
        elif error in ("expired_token", "access_denied"):
            print(f"\n[Copilot] Authorization failed: {error}")
            return None

    print("\n[Copilot] Timed out waiting for authorization.")
    return None


_jwt_cache: dict[str, tuple[str, float]] = {}
_JWT_REFRESH_MARGIN_SECONDS = 120
_TOKEN_EXCHANGE_URL = "https://api.github.com/copilot_internal/v2/token"


def _token_fingerprint(raw_token: str) -> str:
    import hashlib
    return hashlib.sha256(raw_token.encode()).hexdigest()[:16]


def exchange_copilot_token(raw_token: str, timeout: float = 10.0) -> tuple[str, float]:
    """Exchange a raw GitHub token for a Copilot API token."""
    import urllib.request
    fp = _token_fingerprint(raw_token)

    cached = _jwt_cache.get(fp)
    if cached:
        api_token, expires_at = cached
        if time.time() < expires_at - _JWT_REFRESH_MARGIN_SECONDS:
            return api_token, expires_at

    req = urllib.request.Request(
        _TOKEN_EXCHANGE_URL,
        method="GET",
        headers={
            "Authorization": f"token {raw_token}",
            "User-Agent": "GitHubCopilotChat/0.26.7",
            "Accept": "application/json",
            "Editor-Version": "vscode/1.104.1",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        raise ValueError(f"Copilot token exchange failed: {exc}") from exc

    api_token = data.get("token", "")
    expires_at = data.get("expires_at", 0)
    if not api_token:
        raise ValueError("Copilot token exchange returned empty token")

    expires_at = float(expires_at) if expires_at else time.time() + 1800
    _jwt_cache[fp] = (api_token, expires_at)
    return api_token, expires_at


def get_copilot_api_token(raw_token: str) -> str:
    """Exchange token with fallback."""
    if not raw_token:
        return raw_token
    try:
        api_token, _ = exchange_copilot_token(raw_token)
        return api_token
    except Exception as exc:
        logger.debug("Copilot exchange failed, using raw: %s", exc)
        return raw_token


def copilot_request_headers() -> dict[str, str]:
    """Standard Copilot API headers."""
    return {
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "AJA-Gateway/1.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent",
    }
