import sys
from pathlib import Path

# Ensure the integration domains (including runtime helpers) are resolvable
_INTEGRATION_ROOT = Path(__file__).resolve().parent
_RUNTIME_DIR = str(_INTEGRATION_ROOT / "runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


def py_cmd(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'"{sys.executable}" -c "{escaped}"'


def make_git_project(tmp_path: Path) -> Path:
    import subprocess

    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "config", "user.name", "AJA Test"], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "add", "."], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, text=True, check=True)
    return root
