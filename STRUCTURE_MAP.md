# AJA Transition & Debugging Map

This document tracks the reorganization of the AJA project from a cluttered root to a standardized monorepo structure, as well as the resolution of critical schema and import errors.

## 1. Directory Reorganization (Phase 2.5)

| Original Path | New Path | Status | Notes |
| :--- | :--- | :--- | :--- |
| `packages/aja-core/` | `libs/aja-core/` | `[x]` | Core logic move. |
| `src/tools/` | `tools/` | `[x]` | Consolidation of dev tools. |
| `test_*.py` (root) | `tests/python/` | `[x]` | Test consolidation. |
| `diag_*.py`, `verify_*.py` | `scripts/` | `[x]` | Utility script relocation. |
| `*.bat`, `*.ps1` (root) | `tools/launchers/` | `[x]` | Moving entry points. |

## 2. Configuration Updates

| File | Change Required | Status |
| :--- | :--- | :--- |
| `aja.json` | Update territory paths. | `[x]` |
| `pyproject.toml` | Update `pythonpath` and `testpaths`. | `[x]` |
| `aja/config.py` | Robustify `PROJECT_ROOT` detection. | `[x]` |

## 3. Debugging Status (Phase 3)

| Issue | Root Cause | Fix | Status |
| :--- | :--- | :--- | :--- |
| **LanceDB Mismatch** | Old 1536D tables vs new 384D code. | Clear tables for 384D re-init. | `[x]` |
| **Test Import Fail** | `import agent` vs `import aja`. | Global search & replace in `tests/`. | `[x]` |
| **Redundant Logic** | Legacy attribute setting in `planner.py`. | Cleaned up and standardized. | `[x]` |

## 4. Verification Checkpoints

- [x] `pytest tests/` runs without collection errors.
- [x] All vector tables standardized to 384D.
- [x] Legacy artifacts (.sqlite3, .agent/) removed.
- [x] Test runtime optimized via deterministic mocks (93 tests in <15s).
