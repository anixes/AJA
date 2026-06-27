"""
AJA Version Bump Tool
=====================
Bumps the project version, promotes [Unreleased] in CHANGELOG.md to the new
version entry, and optionally commits and tags the release.

Usage
-----
    python tools/bump_version.py patch             # 0.1.0 -> 0.1.1
    python tools/bump_version.py minor             # 0.1.0 -> 0.2.0
    python tools/bump_version.py major             # 0.1.0 -> 1.0.0

    # Preview without writing anything
    python tools/bump_version.py patch --dry-run

    # Bump, commit, and tag (ready to push)
    python tools/bump_version.py patch --commit --tag

    # Bump and tag but skip the commit (if you want to amend manually)
    python tools/bump_version.py patch --tag --no-commit

Exit codes
----------
    0  Success.
    1  Usage / validation error.
    2  Git operation failed.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(ROOT), *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"[git error] {' '.join(args)}\n{result.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return result


def _git_available() -> bool:
    r = _run_git("rev-parse", "--is-inside-work-tree", check=False)
    return r.returncode == 0


# ---------------------------------------------------------------------------
# Version bump
# ---------------------------------------------------------------------------


def _bump_pyproject(part: str, dry_run: bool) -> tuple[str, str]:
    """Return (old_version, new_version) and write pyproject.toml."""
    path = ROOT / "pyproject.toml"
    content = path.read_text(encoding="utf-8")

    m = re.search(r'version\s*=\s*"(\d+)\.(\d+)\.(\d+)"', content)
    if not m:
        print("Error: could not find version string in pyproject.toml", file=sys.stderr)
        sys.exit(1)

    major, minor, patch = map(int, m.groups())
    old_version = f"{major}.{minor}.{patch}"

    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    elif part == "patch":
        patch += 1
    else:
        print(f"Error: part must be major, minor, or patch — got {part!r}", file=sys.stderr)
        sys.exit(1)

    new_version = f"{major}.{minor}.{patch}"
    new_content = content.replace(f'version = "{old_version}"', f'version = "{new_version}"')

    if not dry_run:
        path.write_text(new_content, encoding="utf-8")

    return old_version, new_version


def _bump_init(new_version: str, dry_run: bool) -> Path | None:
    """Update __version__ in aja/__init__.py if the file exists."""
    init_path = ROOT / "libs" / "aja-core" / "aja" / "__init__.py"
    if not init_path.exists():
        return None

    content = init_path.read_text(encoding="utf-8")
    if "__version__" in content:
        new_content = re.sub(
            r'__version__\s*=\s*".*?"',
            f'__version__ = "{new_version}"',
            content,
        )
    else:
        new_content = content + f'\n__version__ = "{new_version}"\n'

    if not dry_run:
        init_path.write_text(new_content, encoding="utf-8")
    return init_path


# ---------------------------------------------------------------------------
# CHANGELOG promotion
# ---------------------------------------------------------------------------

_UNRELEASED_HEADER = "## [Unreleased]"

_EMPTY_UNRELEASED = """\
## [Unreleased]

### Added

### Changed

### Fixed

"""


def _promote_changelog(new_version: str, dry_run: bool) -> bool:
    """
    Move the [Unreleased] section content into a dated [X.Y.Z] section and
    insert a fresh empty [Unreleased] block above it.

    Returns True if the changelog was (or would be) modified.
    """
    changelog_path = ROOT / "CHANGELOG.md"
    if not changelog_path.exists():
        print("Warning: CHANGELOG.md not found — skipping.", file=sys.stderr)
        return False

    content = changelog_path.read_text(encoding="utf-8")

    if _UNRELEASED_HEADER not in content:
        print("Warning: [Unreleased] section not found in CHANGELOG.md — skipping.")
        return False

    today = date.today().isoformat()
    new_header = f"## [{new_version}] - {today}"

    # Replace the first occurrence of "## [Unreleased]" with the versioned header
    # and prepend a fresh [Unreleased] block above it.
    new_content = content.replace(
        _UNRELEASED_HEADER,
        _EMPTY_UNRELEASED + new_header,
        1,  # only the first occurrence
    )

    if not dry_run:
        changelog_path.write_text(new_content, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def _git_commit(new_version: str, files: list[Path]) -> None:
    rel_files = [str(f.relative_to(ROOT)) for f in files]
    _run_git("add", *rel_files)
    _run_git("commit", "-m", f"Release v{new_version}")
    print(f"  [git] committed: {', '.join(rel_files)}")


def _git_tag(new_version: str, annotate: bool = True) -> None:
    tag = f"v{new_version}"
    if annotate:
        _run_git("tag", "-a", tag, "-m", f"Release {tag}")
    else:
        _run_git("tag", tag)
    print(f"  [git] tagged: {tag}")


def _tag_exists(new_version: str) -> bool:
    r = _run_git("tag", "--list", f"v{new_version}", check=False)
    return bool(r.stdout.strip())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump AJA version, promote CHANGELOG, and optionally commit + tag.")
    parser.add_argument(
        "part",
        choices=["major", "minor", "patch"],
        help="Which part of the version to increment.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without writing or running git commands.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Stage changed files and create a release commit.",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Create an annotated git tag for the new version.",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip the commit even if --commit is set (useful with --tag only).",
    )
    args = parser.parse_args()

    want_git = (args.commit or args.tag) and not args.dry_run
    if want_git and not _git_available():
        print("Error: not inside a git repository — cannot commit or tag.", file=sys.stderr)
        return 2

    # 1. Bump pyproject.toml
    old_version, new_version = _bump_pyproject(args.part, args.dry_run)
    print(f"{'[DRY-RUN] ' if args.dry_run else ''}version: {old_version} → {new_version}")

    changed: list[Path] = [ROOT / "pyproject.toml"]

    # 2. Bump __init__.py
    init_path = _bump_init(new_version, args.dry_run)
    if init_path:
        changed.append(init_path)
        print(f"  updated: {init_path.relative_to(ROOT)}")

    # 3. Promote CHANGELOG.md
    changelog_path = ROOT / "CHANGELOG.md"
    if _promote_changelog(new_version, args.dry_run):
        changed.append(changelog_path)
        print(f"  promoted CHANGELOG.md [Unreleased] → [{new_version}]")

    if args.dry_run:
        print("\n[DRY-RUN] No files were written. Re-run without --dry-run to apply.")
        return 0

    # 4. Git commit
    if args.commit and not args.no_commit:
        _git_commit(new_version, changed)

    # 5. Git tag
    if args.tag:
        if _tag_exists(new_version):
            print(f"  [git] tag v{new_version} already exists — skipping.")
        else:
            _git_tag(new_version)

    print(f"\nDone. v{new_version} is ready.")
    if args.tag and not args.commit:
        print(f"  Push with: git push && git push origin v{new_version}")
    elif args.commit and args.tag:
        print(f"  Push with: git push --follow-tags")

    return 0


if __name__ == "__main__":
    sys.exit(main())
