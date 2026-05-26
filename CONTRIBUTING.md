# Contributing to AJA Runtime

Welcome! This guide outlines how to configure your native compiler toolchains, compile local binary wheel extensions, run the test suites, and follow our coarse-grained native design boundaries.

---

## 1. Native Toolchain Setup

AJA compiles native extensions using **Maturin** and **PyO3**. To get started:

### Prerequisites
* **Rust compiler (Stable)**: [Install via Rustup](https://rustup.rs/)
* **Python 3.11+**: Standard developer interpreter.
* **Maturin**:
  ```bash
  pip install maturin
  ```

### Development container (Recommended)
We include a standard cloud-agnostic `.devcontainer/devcontainer.json` workspace pre-configured with the complete Rust toolchain, VS Code extensions, and Maturin setups. Simply open this workspace inside VS Code to onboard in seconds.

---

## 2. Compiling & Developing

### Local Editable Installs
To compile and mount the Rust module dynamically into your local Python development environment:
```bash
maturin develop --manifest-path packages/aja-native/Cargo.toml
```

### Compiling Production Wheels
To package the native module into standard binary distribution wheels:
```bash
maturin build --release --manifest-path packages/aja-native/Cargo.toml
```

The resulting `.whl` files are placed inside the `target/wheels/` or root `dist/` directory.

---

## 3. Running Test Suites

We enforce strict asynchronous transport stability checks. Run tests using `pytest` and `anyio` (the designated async engine):

```bash
$env:PYTHONPATH="libs/aja-core"
python -m pytest tests/python/test_execution_stream_runtime.py
```

---

## 4. PyO3 Boundary Guidelines ("Chunky not Chatty")

When writing code crossing the Python-Rust pyo3 boundary, **always prioritize coarse-grained batch actions over fine-grained, high-frequency boundary crossings**:
1. **Batch data transfers**: Pass message arrays or contiguous buffers to Rust in one call (e.g. `PyTrajectoryManager.analyze` or `count_tokens_batch`).
2. **Minimize GIL Lock contention**: Write heavy algorithms natively in Rust, releasing the GIL where appropriate.
3. **Persist durably**: Delegate file-based or record-based appending (like `timeline.jsonl`) to safe asynchronous writers.
