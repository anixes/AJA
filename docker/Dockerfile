# syntax=docker/dockerfile:1.4
FROM rust:1.75-slim as builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Set up virtual environment and install maturin
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir maturin

# Build the Rust extension
WORKDIR /build/packages/aja-native
COPY packages/aja-native/ .
RUN maturin build --release --out /build/dist

# ---------------------------------------------------------
# Production image
# ---------------------------------------------------------
FROM python:3.11-slim as runtime

RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy built wheels from builder
COPY --from=builder /build/dist /app/dist

# Copy core Python package
COPY libs/aja-core /app/libs/aja-core

# Install aja-native wheel, then aja-core
RUN pip install --no-cache-dir --find-links=/app/dist aja-native && \
    pip install --no-cache-dir -e /app[all]

# Set standard data directory for the container environment
ENV AJA_DATA_DIR=/data/aja
RUN mkdir -p /data/aja

# Default command
CMD ["aja", "run", "--help"]
