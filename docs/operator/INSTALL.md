# AJA Installation Guide

AJA is designed as an installable systems software and durable execution runtime.

## Prerequisites
- **OS**: Windows, macOS, or Linux
- **Python**: 3.11, 3.12, or 3.13
- **Rust**: Stable toolchain (if compiling from source)

## Method 1: Local Pip Installation (Recommended)

You can install AJA as a standard Python package.

```bash
# Clone the repository
git clone https://github.com/your-org/aja.git
cd aja

# Install with Maturin
pip install maturin
maturin develop --release
# Alternatively: pip install .[all]
```

Verify the installation:
```bash
aja doctor
```

## Method 2: Docker / Containerized

AJA provides an official Dockerfile for production environments.

```bash
# Build the production image
docker build -t aja:latest -f docker/Dockerfile .

# Run AJA using Docker Compose
docker-compose -f docker/docker-compose.yml up -d
```

For development with Docker, you can use `.devcontainer` or the `Dockerfile.dev`.

## Post-Installation

Once installed, AJA utilizes a normalized data directory for its LanceDB vector store, configuration, and execution memory.
By default, this is located at:
- **Windows**: `C:\Users\<User>\AppData\Local\Anixes\AJA`
- **macOS**: `~/Library/Application Support/AJA`
- **Linux**: `~/.local/share/AJA`

You can override this by setting the `AJA_DATA_DIR` environment variable.

Run `aja setup` to initialize the directory and configuration.
