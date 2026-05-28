# syntax=docker/dockerfile:1.6
# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust via rustup with a pinned toolchain version
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
    sh -s -- -y --default-toolchain 1.77.2 --no-modify-path && \
    chmod -R a+w /usr/local/rustup /usr/local/cargo

RUN pip install --no-cache-dir maturin

WORKDIR /build

# Copy Rust source separately to maximize layer cache reuse.
# Changes to Python source will NOT invalidate the Rust compile cache.
COPY packages/ ./packages/
COPY pyproject.toml ./

# Copy Python source (invalidates only pip install step, not Rust compile)
COPY libs/ ./libs/

# Build the unified wheel
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/packages/aja-native/target \
    maturin build --release --out /build/dist && \
    rm -f /build/dist/aja_native-*.whl

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash aja
WORKDIR /app
RUN chown aja:aja /app

COPY --from=builder /build/dist /app/dist

RUN set -ex && \
    WHEEL=$(ls /app/dist/aja-*.whl | grep -v aja_native | head -1) && \
    test -n "${WHEEL}" && \
    pip install --no-cache-dir "${WHEEL}[all]"

ENV AJA_DATA_DIR=/data/aja
RUN mkdir -p /data/aja && chown aja:aja /data/aja

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD aja doctor || exit 1

USER aja
CMD ["aja", "--help"]
