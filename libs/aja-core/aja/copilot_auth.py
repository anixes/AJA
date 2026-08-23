"""GitHub Copilot authentication utilities for AJA.

Implements the OAuth device code flow and token exchange for the Copilot API.

Token storage security notes:
- Resolution order for the raw GitHub device-flow token:
  1. In-memory session cache
  2. OS keyring (preferred persistent store):
     ``keyring.get_password("AJA", "copilot")``
  3. Environment variables (including the python-dotenv-loaded
     ``PROJECT_ROOT/.env`` copy): ``COPILOT_GITHUB_TOKEN``, ``GH_TOKEN``,
     ``GITHUB_TOKEN``
  4. ``gh auth token`` CLI fallback
  Keyring unavailability or backend errors (common on headless Linux) are
  swallowed at debug level and resolution falls through to the next source.
- Dual-write rationale: on successful device-flow login the token is written
  BOTH to the OS keyring AND to the ACL-restricted plaintext
  ``PROJECT_ROOT/.env`` (gitignored; ACLs restricted after every write via
  icacls on Windows / chmod 0o600 on POSIX). The .env copy remains as a
  documented fallback so headless/keyring-less hosts keep working.
- The token is NOT exported into child-process environments by default. Set
  ``AJA_EXPORT_COPILOT_TOKEN=1`` to opt back into exporting
  ``COPILOT_GITHUB_TOKEN`` to ``os.environ``.
- Migration: ``migrate_token_to_keyring()`` copies an existing .env token
  into the keyring when the keyring does not already hold one. It never
  deletes .env content (the fallback copy stays valid); returns True only
  when a migration actually occurred. It is invoked opportunistically
  (best-effort, exception-swallowed) during the login save path after a
  successful keyring write.
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

_EXPORT_TOKEN_FLAG = "AJA_EXPORT_COPILOT_TOKEN"
_KEYRING_SERVICE = "AJA"
_KEYRING_USERNAME = "copilot"


def _keyring_get() -> Optional[str]:
    """Read the Copilot token from the OS keyring. Never raises."""
    try:
        import keyring
    except Exception as exc:
        logger.debug("keyring unavailable; skipping OS keychain lookup: %s", exc)
        return None
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
    except Exception as exc:
        logger.debug("keyring lookup failed (headless backend?): %s", exc)
        return None


def _keyring_set(token: str) -> bool:
    """Persist the Copilot token to the OS keyring. Returns success."""
    try:
        import keyring
    except Exception as exc:
        logger.debug("keyring unavailable; cannot store token: %s", exc)
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, token)
        return True
    except Exception as exc:
        logger.debug("keyring write failed (headless backend?): %s", exc)
        return False


def _read_env_file_token() -> str:
    """Read the raw COPILOT_GITHUB_TOKEN line from PROJECT_ROOT/.env, if any."""
    try:
        from aja.config import PROJECT_ROOT

        env_path = PROJECT_ROOT / ".env"
        if not env_path.exists():
            return ""
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("COPILOT_GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception as exc:
        logger.debug("Could not read .env Copilot token: %s", exc)
    return ""


def _export_token_enabled() -> bool:
    """Whether the raw token should be exported to child-process environments."""
    return os.getenv(_EXPORT_TOKEN_FLAG, "").strip() == "1"

# OAuth device code flow constants
COPILOT_OAUTH_CLIENT_ID = "Iv1.b507a08c87ecfe98"
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
        return (
            False,
            "Classic PATs (ghp_*) are not supported. Use device code or fine-grained PAT.",
        )
    return True, "OK"


def _restrict_file_acl(path) -> None:
    """Restrict a file's permissions to the current user only. Never raises."""
    try:
        if os.name == "nt":
            username = os.environ.get("USERNAME") or Path(path).owner()
            cmd = [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{username}:F",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=False
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to restrict ACLs on %s: %s", path, result.stderr.strip()
                )
        else:
            os.chmod(path, 0o600)
    except Exception as exc:
        logger.warning("Could not restrict file permissions on %s: %s", path, exc)


# In-memory session cache for resolved raw GitHub token: (token, source)
_CACHED_RAW_TOKEN: Optional[tuple[str, str]] = None


def invalidate_copilot_cache() -> None:
    """Invalidate all cached GitHub and Copilot tokens (e.g. after a 401/403)."""
    global _CACHED_RAW_TOKEN, _jwt_cache
    _CACHED_RAW_TOKEN = None
    _jwt_cache.clear()
    os.environ.pop("COPILOT_GITHUB_TOKEN", None)
    logger.info("Copilot token and JWT caches have been invalidated.")


def resolve_copilot_token() -> tuple[str, str]:
    """Resolve a GitHub token suitable for Copilot API use with $O(1)$ in-memory caching."""
    global _CACHED_RAW_TOKEN

    # 1. Fast in-memory cache lookup
    if _CACHED_RAW_TOKEN is not None:
        token, source = _CACHED_RAW_TOKEN
        valid, _ = validate_copilot_token(token)
        if valid:
            return token, source
        _CACHED_RAW_TOKEN = None

    # 2. OS keyring (preferred persistent store; falls through on None/error)
    kr_token = _keyring_get()
    if kr_token:
        valid, msg = validate_copilot_token(kr_token)
        if not valid:
            logger.warning("Token from OS keyring is not supported: %s", msg)
        else:
            _CACHED_RAW_TOKEN = (kr_token, "keyring")
            return kr_token, "keyring"

    # 3. Check environment variables
    for env_var in COPILOT_ENV_VARS:
        val = os.getenv(env_var, "").strip()
        if val:
            valid, msg = validate_copilot_token(val)
            if not valid:
                logger.warning("Token from %s is not supported: %s", env_var, msg)
                continue
            _CACHED_RAW_TOKEN = (val, env_var)
            return val, env_var

    # 4. Fall back to gh auth token CLI fallback (memoized once per session)
    token = _try_gh_cli_token()
    if token:
        valid, msg = validate_copilot_token(token)
        if not valid:
            logger.warning("Token from `gh auth token` is not supported: %s", msg)
        else:
            _CACHED_RAW_TOKEN = (token, "gh auth token")
            # Export to os.environ so child tools/processes inherit it
            # (opt-in via AJA_EXPORT_COPILOT_TOKEN=1)
            if _export_token_enabled():
                os.environ["COPILOT_GITHUB_TOKEN"] = token
            else:
                logger.debug(
                    "Skipping COPILOT_GITHUB_TOKEN export to child-process "
                    "environment (set %s=1 to enable).",
                    _EXPORT_TOKEN_FLAG,
                )
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
        if (
            candidate not in candidates
            and os.path.isfile(candidate)
            and os.access(candidate, os.X_OK)
        ):
            candidates.append(candidate)
    return candidates


def _try_gh_cli_token() -> Optional[str]:
    hostname = os.getenv("COPILOT_GH_HOST", "").strip()
    clean_env = {
        k: v for k, v in os.environ.items() if k not in {"GITHUB_TOKEN", "GH_TOKEN"}
    }
    for gh_path in _gh_cli_candidates():
        cmd = [gh_path, "auth", "token"]
        if hostname:
            cmd += ["--hostname", hostname]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=5, env=clean_env
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def migrate_token_to_keyring() -> bool:
    """Copy an existing .env Copilot token into the OS keyring.

    No-op when the .env token is absent or the keyring already holds a token.
    Never deletes .env content (the plaintext fallback stays valid).
    Returns True only when a migration actually occurred.
    """
    token = _read_env_file_token()
    if not token:
        return False
    if _keyring_get():
        return False
    return _keyring_set(token)


def copilot_device_code_login(
    host: str = "github.com", timeout_seconds: float = 300
) -> Optional[str]:
    """Run the GitHub OAuth device code flow for Copilot."""
    import urllib.request
    import urllib.parse

    domain = host.rstrip("/")
    device_code_url = f"https://{domain}/login/device/code"
    access_token_url = f"https://{domain}/login/oauth/access_token"

    data = urllib.parse.urlencode(
        {"client_id": COPILOT_OAUTH_CLIENT_ID, "scope": "read:user"}
    ).encode()
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

    verification_uri = device_data.get(
        "verification_uri", "https://github.com/login/device"
    )
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
        poll_data = urllib.parse.urlencode(
            {
                "client_id": COPILOT_OAUTH_CLIENT_ID,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
        ).encode()
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
            try:
                print(" ✓\n")
            except UnicodeEncodeError:
                print(" OK\n")
            
            token = result["access_token"]
            # Dual-write: OS keyring first, then the ACL'd .env fallback copy.
            keyring_saved = _keyring_set(token)
            try:
                from aja.config import PROJECT_ROOT
                env_path = PROJECT_ROOT / ".env"
                existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
                new_lines = [line for line in existing_lines if not line.startswith("COPILOT_GITHUB_TOKEN=")]
                new_lines.append(f"COPILOT_GITHUB_TOKEN={token}")
                env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                _restrict_file_acl(env_path)

                if keyring_saved:
                    try:
                        migrate_token_to_keyring()
                    except Exception as exc:
                        logger.debug(
                            "Opportunistic keyring migration failed: %s", exc
                        )

                # Update current session environment so it works immediately
                # (opt-in via AJA_EXPORT_COPILOT_TOKEN=1)
                if _export_token_enabled():
                    os.environ["COPILOT_GITHUB_TOKEN"] = token
                else:
                    logger.debug(
                        "Skipping COPILOT_GITHUB_TOKEN export to child-process "
                        "environment (set %s=1 to enable).",
                        _EXPORT_TOKEN_FLAG,
                    )
            except Exception as e:
                print(f"[Copilot] Warning: Could not save token to .env: {e}")
                
            return token

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


def copilot_request_headers(
    *,
    is_agent_turn: bool = True,
    is_vision: bool = False,
) -> dict[str, str]:
    """Standard Copilot API headers."""
    headers = {
        "Editor-Version": "vscode/1.104.1",
        "User-Agent": "AJA-Gateway/1.0",
        "Copilot-Integration-Id": "vscode-chat",
        "Openai-Intent": "conversation-edits",
        "x-initiator": "agent" if is_agent_turn else "user",
    }
    if is_vision:
        headers["Copilot-Vision-Request"] = "true"
    return headers
