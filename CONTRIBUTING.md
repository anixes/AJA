# Contributing to AJA Runtime

Welcome! This guide covers environment setup, the native toolchain, test suites, code style, and the PR process.

---

## 1. Prerequisites

* **Python 3.11+** (the project is validated on 3.12.10)
* **Rust toolchain (Stable)**: [Install via Rustup](https://rustup.rs/) — required to build the `aja_native` PyO3 extension
* **Maturin** (installed automatically as part of `[dev]` extras):
  ```bash
  pip install maturin
  ```
* **Windows**: `pywinpty` enables the real ConPTY path (`pip install -e ".[pty-win]"`)
* **Development container (recommended)**: open the workspace in VS Code — `.devcontainer/devcontainer.json` ships with Rust, maturin, and extensions preconfigured.

## 2. Development Setup

From the repository root:

```bash
# Full editable install including native module + all optional integrations
pip install -e ".[all,dev]"

# Or a minimal dev setup
pip install -e ".[dev]"
```

Recompile the Rust extension after editing `packages/aja-native/`:

```bash
maturin develop --release --manifest-path packages/aja-native/Cargo.toml
```

Production wheels land in `target/wheels/`:

```bash
maturin build --release --manifest-path packages/aja-native/Cargo.toml
```

Pre-commit hooks are not currently configured; run `ruff check` and `ruff format` manually before submitting.

## 3. Running Tests

Full parallel suite (~2 min, per-worker data isolation is automatic):

```bash
py -3.12 -m pytest tests/python -n 8 --dist loadgroup --timeout=420
```

Serial baseline (~20–25 min):

```bash
py -3.12 -m pytest tests/python
```

### Benchmarks

Performance baseline measurements (classify latency, embeddings, LanceDB round-trips, journal throughput):

```bash
py -3.12 -m pytest tests/python -m benchmark
```

### Provider Conformance (live APIs)

Per-provider LLM checks — auto-skips unconfigured providers:

```bash
py -3.12 -m pytest tests/python/live -m live_providers
```

Other markers: `unit`, `integration`, `planning`, `slow`, `e2e`, `live_web` (see `pyproject.toml [tool.pytest.ini_options]`).

## 4. Code Style Expectations

* **Type hints**: annotate public functions and class attributes; mypy runs with `warn_return_any` and friends.
* **Docstrings**: required for public modules, classes, and non-obvious functions.
* **Formatting**: `ruff format` (line length 120, double quotes).
* **Linting**: `ruff check` — see configured rules in `pyproject.toml`.
* **Error handling**: no silent `except Exception: pass`. Best-effort paths get a `# best-effort:` comment; decision-relevant failures are logged or raised. Follow existing structured-error patterns (typed exceptions, journal events).
* **Async hygiene**: use `anyio.create_task_group()` in tests, off-loop for blocking I/O.

## 5. PyO3 Boundary Guidelines ("Chunky not Chatty")

1. **Batch data transfers**: pass arrays/buffers in one call (e.g. `count_tokens_batch`), not per-item calls.
2. **Minimize GIL contention**: heavy algorithms live natively in Rust; release the GIL where appropriate.
3. **Fail loudly across FFI**: raise clean `PyValueError`/`PyIOError`, never panic across the boundary.

## 6. Pull Request Process

1. **Branch naming**: `feat/<short-desc>`, `fix/<issue-ref>`, `docs/<topic>`, `chore/<topic>`.
2. **Read `AGENTS.md` first** — it contains architecture context, recent phases, verification commands, and known traps (e.g. xdist worker isolation).
3. **Tests required**: new behavior needs tests; run the full parallel suite locally before pushing.
4. **Keep scope tight**: don't leave temporary baton files behind; temp artifacts belong in `libs/aja-core/temp_batons` or system tmp.
5. **PR description**: what changed, why, how it was verified (include test counts).
