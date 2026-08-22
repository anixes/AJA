"""
AJA Startup Validation Checks
=============================
Fast, fail-fast/warn-loudly configuration validation at CLI bootstrap.

Design constraints:
- NO network calls, NO LanceDB opens, NO heavy imports — must complete in <200ms.
- Pure stdlib + os.environ reads; config is passed in or read trivially from JSON.

NOTE: Some helper semantics (provider key resolution) intentionally mirror
aja/llm.py::get_llm_gateway and aja/utils/diagnostics.py, which we must not
import here to keep bootstrap lightweight (they pull heavy dependencies).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# provider prefix -> candidate API key env names (any one set => OK)
# Mirrors aja/llm.py key resolution: google falls back GEMINI_API_KEY.
PROVIDER_KEY_ENVS: Dict[str, List[str]] = {
    "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "llama_cpp": [],  # local server; no key required
    "copilot": [],  # device-flow token file auth; no static env key
}

MODEL_ROLES = ("planner", "worker", "critic")


@dataclass
class CheckResult:
    name: str
    severity: str  # "error" | "warning" | "ok"
    detail: str


def _parse_provider(model_str: str) -> Optional[str]:
    """Parse 'provider:model' prefixes. Returns None for bare model names."""
    if not model_str or ":" not in model_str:
        return None
    return model_str.split(":", 1)[0].strip().lower() or None


def _config_models(config: Any) -> Dict[str, Any]:
    """Extract {role: model_str} from an injected config (dict or pydantic)."""
    if config is None:
        return {}
    if hasattr(config, "model_dump"):
        try:
            config = config.model_dump()
        except Exception:
            return {}
    if isinstance(config, dict):
        models = (config.get("swarm_settings") or {}).get("models") or {}
        if isinstance(models, dict):
            return models
    return {}


def _resolve_model(role: str, env_name: str, config: Any = None) -> str:
    """
    Resolve the effective model string for a role.
    Priority: env var -> injected/config swarm_settings.models.<role> ->
    planner's value (critic only) -> built-in default.
    Mirrors aja/config.py semantics without importing heavy modules when a
    config object is supplied.
    """
    val = os.getenv(env_name)
    if val:
        return val
    models = _config_models(config)
    if models.get(role):
        return str(models[role])
    if config is None:
        # Mirror aja/config.py's already-resolved module constants exactly.
        try:
            from aja.config import (
                AJA_CRITIC_MODEL,
                AJA_PLANNER_MODEL,
                AJA_WORKER_MODEL,
            )

            resolved = {
                "planner": AJA_PLANNER_MODEL,
                "worker": AJA_WORKER_MODEL,
                "critic": AJA_CRITIC_MODEL,
            }
            return str(resolved.get(role) or "")
        except Exception:
            # best-effort: config import failure should never crash bootstrap
            pass
    if role == "critic":
        # Critic defaults to the planner model (mirrors aja/config.py:136-141).
        return os.getenv("AJA_PLANNER_MODEL") or str(models.get("planner") or "")
    return ""


def check_model_api_keys(config: Any = None) -> List[CheckResult]:
    """Validate that each role's model references a provider with a key present."""
    results: List[CheckResult] = []
    seen_providers: Dict[str, CheckResult] = {}
    role_envs = {
        "planner": "AJA_PLANNER_MODEL",
        "worker": "AJA_WORKER_MODEL",
        "critic": "AJA_CRITIC_MODEL",
    }
    for role in MODEL_ROLES:
        model = _resolve_model(role, role_envs[role], config)
        provider = _parse_provider(model)
        if provider is None:
            results.append(
                CheckResult(
                    name=f"Model Key ({role})",
                    severity="ok",
                    detail=f"No explicit provider prefix in '{model or '<unset>'}'; skipped.",
                )
            )
            continue

        if provider in seen_providers:
            cached = seen_providers[provider]
            # Re-emit the same verdict under this role's name.
            results.append(
                CheckResult(
                    name=f"Model Key ({role})",
                    severity=cached.severity,
                    detail=cached.detail,
                )
            )
            continue

        if provider not in PROVIDER_KEY_ENVS:
            result = CheckResult(
                name=f"Model Key ({role})",
                severity="warning",
                detail=(
                    f"Unknown provider '{provider}' in '{model}' — cannot verify "
                    f"an API key automatically."
                ),
            )
        else:
            candidates = PROVIDER_KEY_ENVS[provider]
            if not candidates:
                result = CheckResult(
                    name=f"Model Key ({role})",
                    severity="ok",
                    detail=f"Provider '{provider}' requires no static API key.",
                )
            elif any(os.getenv(env, "").strip() for env in candidates):
                result = CheckResult(
                    name=f"Model Key ({role})",
                    severity="ok",
                    detail=f"Provider '{provider}' key found via {candidates[0]}-family env.",
                )
            else:
                result = CheckResult(
                    name=f"Model Key ({role})",
                    severity="error",
                    detail=(
                        f"{model} requires {' or '.join(candidates)} "
                        f"but none are set."
                    ),
                )
        seen_providers[provider] = result
        results.append(result)
    return results


def _find_baton_endpoints(obj: Any, path: str = "", under_baton: bool = False) -> List[str]:
    """
    Recursively find config keys hinting at remote baton endpoints.
    Matches either a key containing both 'baton' and an endpoint token, or any
    key with an endpoint token nested beneath a 'baton'-ish ancestor.
    """
    endpoint_tokens = ("endpoint", "url", "host")
    hits: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            p = f"{path}.{k}" if path else str(k)
            is_baton_key = "baton" in kl
            if (is_baton_key and any(t in kl for t in endpoint_tokens)) or (
                (under_baton or is_baton_key)
                and not is_baton_key
                and any(t in kl for t in endpoint_tokens)
            ):
                hits.append(p)
            hits.extend(_find_baton_endpoints(v, p, under_baton or is_baton_key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits.extend(_find_baton_endpoints(v, f"{path}[{i}]", under_baton))
    return hits


def check_baton_security(config: Any = None) -> CheckResult:
    """Baton transfer HMAC symmetry check."""
    secret_set = bool(os.getenv("AJA_BATON_SECRET", "").strip())
    if secret_set:
        return CheckResult(
            name="Baton Security",
            severity="ok",
            detail="AJA_BATON_SECRET is set; baton transfers are HMAC-signed.",
        )

    endpoints: List[str] = []
    if config is None:
        # Trivial read of aja.json only — no pydantic validation, no side effects.
        for candidate in (_config_path(),):
            try:
                if candidate.exists():
                    config = json.loads(candidate.read_text(encoding="utf-8"))
                    break
            except Exception as e:
                logger.debug("Could not read %s: %s", candidate, e)

    if config is not None:
        raw = config.model_dump() if hasattr(config, "model_dump") else config
        endpoints = _find_baton_endpoints(raw)

    if endpoints:
        return CheckResult(
            name="Baton Security",
            severity="warning",
            detail=(
                "AJA_BATON_SECRET is unset but remote baton endpoint(s) configured "
                f"({', '.join(endpoints)}); transfers will be unauthenticated."
            ),
        )
    return CheckResult(
        name="Baton Security",
        severity="ok",
        detail="No remote baton endpoints configured; skipping.",
    )


def _config_path() -> Path:
    try:
        from aja.config import CONFIG_PATH

        return Path(CONFIG_PATH)
    except Exception:
        # best-effort fallback for isolated unit-test contexts
        return Path.home() / ".aja" / "aja.json"


_RETENTION_TOKENS = ("retention", "ttl")


def check_retention(config: Any = None) -> CheckResult:
    """Validate retention/ttl knobs (if any exposed by config) are positive ints."""
    if config is None:
        try:
            from aja.config import CONFIG as cfg

            config = cfg
        except Exception:
            config = None

    knobs: Dict[str, Any] = {}
    if config is not None and hasattr(config, "model_dump"):
        dump = config.model_dump()

        def walk(node: Any, path: str = "") -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    kl = str(k).lower()
                    p = f"{path}.{k}" if path else str(k)
                    if any(t in kl for t in _RETENTION_TOKENS):
                        knobs[p] = v
                    else:
                        walk(v, p)
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{path}[{i}]")

        walk(dump)

    bad = {
        k: v
        for k, v in knobs.items()
        if not (isinstance(v, int) and not isinstance(v, bool) and v > 0)
    }
    if not knobs:
        return CheckResult(
            name="Retention Sanity",
            severity="ok",
            detail="Config exposes no retention/ttl knobs; nothing to validate.",
        )
    if bad:
        return CheckResult(
            name="Retention Sanity",
            severity="error",
            detail=f"Non-positive/non-int retention values: {bad}",
        )
    keys = ", ".join(sorted(knobs))
    return CheckResult(
        name="Retention Sanity",
        severity="ok",
        detail=f"All retention knobs valid: {keys}",
    )


def check_data_dir_writable(data_dir: Optional[Path] = None) -> CheckResult:
    """DATA_DIR exists or can be created AND is writable."""
    if data_dir is None:
        data_dir = Path(os.getenv("AJA_DATA_DIR", "")) if os.getenv("AJA_DATA_DIR") else None
        if data_dir is None:
            try:
                from aja.config import DATA_DIR

                data_dir = Path(DATA_DIR)
            except Exception:
                # last-resort platform default without importing aja.config
                import platformdirs

                data_dir = Path(platformdirs.user_data_dir("AJA", "Anixes"))

    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".aja_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return CheckResult(
            name="Data Dir Writability",
            severity="ok",
            detail=f"{data_dir} is writable.",
        )
    except Exception as e:
        return CheckResult(
            name="Data Dir Writability",
            severity="error",
            detail=f"{data_dir} is not writable: {e}",
        )


def run_startup_checks(config: Any = None) -> List[CheckResult]:
    """Run all fast startup validation checks. No network, no DB opens."""
    results: List[CheckResult] = []
    results.extend(check_model_api_keys(config))
    results.append(check_baton_security(config))
    results.append(check_retention(config))
    results.append(check_data_dir_writable())
    return results


def format_startup_checks(results: List[CheckResult], file: Any = None) -> int:
    """
    Print results grouped by severity (warnings first, errors LAST, ok dim).
    Pass file=sys.stderr to keep stdout clean (e.g. agent-mode JSON contracts).
    Returns the count of error-severity results.
    """
    errors = [r for r in results if r.severity == "error"]
    warnings = [r for r in results if r.severity == "warning"]
    oks = [r for r in results if r.severity == "ok"]

    icons = {"error": "[ERR]", "warning": "[WARN]", "ok": "[OK]"}
    styles = {
        "error": "\033[91m",
        "warning": "\033[93m",
        "ok": "\033[90m",
    }

    def emit(group: List[CheckResult]) -> None:
        for r in group:
            print(
                f"{styles[r.severity]}{icons[r.severity]} {r.name}: {r.detail}\033[0m",
                file=file,
            )

    try:
        emit(warnings)
        emit(oks)
        emit(errors)  # errors last so they are visible above any exit banner
    except UnicodeEncodeError:
        pass

    return len(errors)
