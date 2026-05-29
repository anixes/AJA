# Local CI Simulation & Release Certification Workflow

This directory contains `release_check.py`, the canonical local release validator for AJA. This script replicates the GitHub Actions CI environment deterministically on your local machine to catch packaging, runtime, and integration failures *before* pushing to GitHub.

## `release_check.py`

This is a pure Python script that builds and validates a clean release artifact.

### Phases Executed
1. **Environment Contamination Audit:** Verifies you aren't shadowed by `PYTHONPATH` or an existing editable install.
2. **Stale Artifact Detection:** Warns/fails if `dist/`, `build/`, or `target/` contain stale binaries that could contaminate the build.
3. **Cleanroom Setup:** Provisions a fresh, isolated `venv` solely for CI validation.
4. **Build Wheels:** Compiles the `aja` and `aja_native` wheels via `maturin --release`.
5. **Clean Wheel Install Test:** Uninstalls local packages and installs only the built wheel, strictly verifying that AJA imports from `site-packages` and not a local shadow directory.
6. **Validation:** Executes `aja doctor --ci` and runs the full Pytest suite against the built wheel.
7. **Docker Certification:** Replicates `docker-publish.yml` by building and running smoke tests inside an isolated Docker container.

### Usage

Run standard developer validation:
```bash
python scripts/release_check.py
```
*(If Docker is not running, Phase 7 will be skipped with a non-blocking warning.)*

Run strict pre-release certification:
```bash
python scripts/release_check.py --strict
```
*(In strict mode: warnings become blocking errors, stale artifacts fail the build, and Docker is mandatory. Use this right before cutting a release.)*

---

## `act` Integration (Optional Workflow Emulator)

While `release_check.py` is the canonical AJA-specific release validator, you may also use [act](https://github.com/nektos/act) if you need to test the exact syntax of the `.github/workflows` YAML files. 

`act` is treated as an optional workflow reproduction layer, kept strictly separate from `release_check.py` to prevent platform divergence and coupling.

**To test GitHub Actions locally using `act`:**

Run a specific job (e.g., `validate_wheels`):
```bash
act -j validate_wheels
```

Run a specific workflow (e.g., `ci.yml`):
```bash
act -W .github/workflows/ci.yml
```

> **Note:** Running `act` requires a Docker daemon and uses heavily virtualized runner images. For routine pre-push validation of the AJA runtime, prefer `python scripts/release_check.py`.
