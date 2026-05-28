FROM rust:1.85-slim as builder

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

# Build the Rust extension AND Python package
WORKDIR /build
COPY . .
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

# Install the built wheel
RUN pip install --no-cache-dir /app/dist/aja*.whl[all]

# Set standard data directory for the container environment
ENV AJA_DATA_DIR=/data/aja
RUN mkdir -p /data/aja

# Default command
CMD ["aja", "run", "--help"]
