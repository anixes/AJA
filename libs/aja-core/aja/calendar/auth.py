"""Google Calendar OAuth token management for AJA.

Token storage convention mirrors ``aja.copilot_auth``:
- Preferred store: OS keyring under service "AJA", username "gcal".
- Fallback copy: an ACL-restricted ``GCAL_REFRESH_TOKEN=...`` line in
  ``PROJECT_ROOT/.env`` (gitignored; permissions restricted after write).
- Client configuration comes from the environment:
  - ``GOOGLE_CALENDAR_CLIENT_SECRET``: path to a downloaded OAuth
    ``client_secret.json`` file (desktop app), OR
  - ``GOOGLE_CALENDAR_CLIENT_ID`` + ``GOOGLE_CALENDAR_CLIENT_SECRET``
    raw values.

All google library imports are guarded so the feature degrades cleanly
when the optional dependencies are absent.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_KEYRING_SERVICE = "AJA"
_KEYRING_USERNAME = "gcal"
_ENV_FALLBACK_KEY = "GCAL_REFRESH_TOKEN"
_SCOPES = ["https://www.googleapis.com/auth/calendar"]
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"

try:  # pragma: no cover - exercised implicitly when deps installed
    import google.auth.transport.requests as _google_requests
    import google.oauth2.credentials as _google_credentials
    from googleapiclient.discovery import build as _discovery_build

    GOOGLE_LIBS_AVAILABLE = True
except ImportError:  # pragma: no cover
    GOOGLE_LIBS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Keyring helpers (exception-swallowing, mirroring copilot_auth)
# ---------------------------------------------------------------------------

def _keyring_get() -> Optional[str]:
    """Read the stored Google Calendar refresh token. Never raises."""
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
    """Persist the refresh token to the OS keyring. Returns success."""
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


def _keyring_delete() -> bool:
    """Delete the stored refresh token from the OS keyring. Never raises."""
    try:
        import keyring
    except Exception as exc:
        logger.debug("keyring unavailable; cannot delete token: %s", exc)
        return False
    try:
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
        return True
    except Exception as exc:
        logger.debug("keyring delete failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# .env fallback storage (minimal local implementation, no copilot_auth import)
# ---------------------------------------------------------------------------

def _env_file_path() -> Path:
    try:
        from aja.config import PROJECT_ROOT

        base = Path(PROJECT_ROOT)
    except Exception:
        base = Path.home() / ".aja"
    return base / ".env"


def _restrict_file_acl(path: Path) -> None:
    """Restrict a file's permissions to the current user only. Never raises."""
    try:
        if os.name == "nt":
            username = os.environ.get("USERNAME") or path.owner()
            result = subprocess.run(
                ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "Failed to restrict ACLs on %s: %s", path, result.stderr.strip()
                )
        else:
            os.chmod(path, 0o600)
    except Exception as exc:
        logger.warning("Could not restrict file permissions on %s: %s", path, exc)


def _read_env_fallback() -> str:
    """Read GCAL_REFRESH_TOKEN from the .env fallback, if present."""
    env_path = _env_file_path()
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{_ENV_FALLBACK_KEY}="):
            return line.split("=", 1)[1].strip()
    return ""


def _write_env_fallback(token: str) -> bool:
    """Write/replace the GCAL_REFRESH_TOKEN line in .env with restricted ACLs."""
    try:
        env_path = _env_file_path()
        existing = (
            env_path.read_text(encoding="utf-8").splitlines()
            if env_path.exists()
            else []
        )
        kept = [
            line for line in existing if not line.startswith(f"{_ENV_FALLBACK_KEY}=")
        ]
        kept.append(f"{_ENV_FALLBACK_KEY}={token}")
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        _restrict_file_acl(env_path)
        return True
    except Exception as exc:
        logger.warning("Could not persist Google Calendar token fallback: %s", exc)
        return False


def _remove_env_fallback() -> bool:
    try:
        env_path = _env_file_path()
        if not env_path.exists():
            return True
        kept = [
            line
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if not line.startswith(f"{_ENV_FALLBACK_KEY}=")
        ]
        env_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug("Could not remove Google Calendar token fallback: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Token resolution + client configuration
# ---------------------------------------------------------------------------

def resolve_refresh_token() -> str:
    """Resolution order: keyring first, then the ACL'd .env fallback."""
    token = _keyring_get()
    if token:
        return token
    return _read_env_fallback()


def load_client_config() -> Optional[Dict[str, Any]]:
    """Load OAuth client configuration from the environment.

    Returns a dict shaped like a downloaded client_secret.json (with an
    "installed" or "web" key), or None when nothing is configured.
    """
    raw = os.getenv("GOOGLE_CALENDAR_CLIENT_SECRET", "").strip()
    client_id_env = os.getenv("GOOGLE_CALENDAR_CLIENT_ID", "").strip()

    if not raw:
        return None

    # Interpretation order: existing file path first, else raw secret value.
    candidate = Path(raw)
    if candidate.is_file():
        try:
            return json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Could not parse client secret file %s: %s", candidate, exc)
            return None

    if client_id_env:
        return {
            "installed": {
                "client_id": client_id_env,
                "client_secret": raw,
                "auth_uri": _AUTH_URI,
                "token_uri": _TOKEN_URI,
                "redirect_uris": ["http://localhost"],
            }
        }

    logger.error(
        "GOOGLE_CALENDAR_CLIENT_SECRET is neither a readable client_secret.json "
        "file nor paired with GOOGLE_CALENDAR_CLIENT_ID."
    )
    return None


def is_connected() -> bool:
    """True when a refresh token is available (keyring preferred)."""
    return bool(resolve_refresh_token())


def _persist_refresh_token(token: str) -> None:
    """Dual-write: keyring first, then the ACL-restricted .env fallback."""
    keyring_saved = _keyring_set(token)
    fallback_saved = _write_env_fallback(token)
    if not keyring_saved and fallback_saved:
        logger.info(
            "Google Calendar token saved to %s fallback only "
            "(keyring backend unavailable).",
            _env_file_path(),
        )


def get_service():
    """Build an authorized googleapiclient Calendar service.

    Raises:
        ImportError: when the optional google libraries are not installed.
        RuntimeError: when no refresh token or no OAuth client config exists.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        raise ImportError(
            "Google Calendar support requires the optional dependencies "
            "'google-api-python-client' and 'google-auth-oauthlib'. "
            "Install them to enable calendar features."
        )

    refresh_token = resolve_refresh_token()
    if not refresh_token:
        raise RuntimeError(
            "Google Calendar is not connected. Run 'aja calendar connect' first."
        )

    client_cfg = load_client_config()
    if client_cfg is None:
        raise RuntimeError(
            "No Google OAuth client configuration found. Set "
            "GOOGLE_CALENDAR_CLIENT_SECRET (path to client_secret.json) or "
            "GOOGLE_CALENDAR_CLIENT_ID plus GOOGLE_CALENDAR_CLIENT_SECRET."
        )
    installed = client_cfg.get("installed") or client_cfg.get("web") or {}
    if "client_id" not in installed or "client_secret" not in installed:
        raise RuntimeError(
            "OAuth client configuration is missing client_id/client_secret fields."
        )

    creds = _google_credentials.Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=installed["client_id"],
        client_secret=installed["client_secret"],
        scopes=_SCOPES,
    )
    if not creds.valid:
        creds.refresh(_google_requests.Request())

    # Persist rotated refresh tokens back to storage.
    new_refresh = getattr(creds, "refresh_token", None) or refresh_token
    if new_refresh != refresh_token:
        logger.info("Google Calendar refresh token was rotated; re-saving.")
        _persist_refresh_token(new_refresh)

    return _discovery_build("calendar", "v3", credentials=creds, cache_discovery=False)


def connect(interactive: bool = True) -> Dict[str, Any]:
    """Run the interactive InstalledApp local-server flow and store tokens.

    Returns a status dict. Raises ImportError/RuntimeError as documented.
    """
    if not GOOGLE_LIBS_AVAILABLE:
        raise ImportError(
            "Google Calendar support requires the optional dependencies "
            "'google-api-python-client' and 'google-auth-oauthlib'."
        )
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ImportError(
            "'google-auth-oauthlib' is required for the connect flow."
        ) from exc

    client_cfg = load_client_config()
    if client_cfg is None:
        raise RuntimeError(
            "No Google OAuth client configuration found. Set "
            "GOOGLE_CALENDAR_CLIENT_SECRET (path to client_secret.json) or "
            "GOOGLE_CALENDAR_CLIENT_ID plus GOOGLE_CALENDAR_CLIENT_SECRET."
        )

    flow = InstalledAppFlow.from_client_config(client_cfg, scopes=_SCOPES)
    if interactive:
        creds = flow.run_local_server(port=0)
    else:  # pragma: no cover - legacy console flow for headless setups
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt_consent="consent")
        print(f"Open this URL in your browser:\n{auth_url}")
        code = input("Enter the authorization code: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials

    token = getattr(creds, "refresh_token", None) or creds.token
    if not token:
        raise RuntimeError("Authorization completed but returned no usable token.")
    _persist_refresh_token(token)
    return {"connected": True}


def disconnect() -> bool:
    """Remove stored Google Calendar tokens (keyring entry + .env line)."""
    removed_keyring = _keyring_delete()
    removed_env = _remove_env_fallback()
    return removed_keyring or removed_env
