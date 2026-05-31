# syntax=docker/dockerfile:1.6
# FR-7.2 — Python worker image: Ubuntu 24.04 + Python 3.11 (TRD §5)
# Multi-stage: builder installs compiled wheels; runtime image is lean (< 1.5 GB).

# ── stage 1: builder — compile all pip packages ───────────────────────────────
FROM ubuntu:24.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Add deadsnakes PPA for Python 3.11, then install Python + build tools.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3.11-venv \
        gcc \
        g++ \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create a virtualenv and install all pinned runtime dependencies.
RUN python3.11 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ── stage 2: runtime — Python 3.11 + pre-built venv only ─────────────────────
FROM ubuntu:24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive
LABEL org.opencontainers.image.title="rct-python" \
      org.opencontainers.image.description="Raleigh Commute Digital Twin — Python 3.11 worker (TRD §5)"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Add deadsnakes PPA for Python 3.11 runtime (no build tools).
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        software-properties-common \
        ca-certificates \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        python3.11 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source into the image (Phase 2 Fargate — no volume mounts)
COPY src/ /app/src/
COPY scripts/ /app/scripts/
COPY config/ /app/config/

WORKDIR /app

# PYTHONPATH must include src/ so all modules resolve correctly
ENV PYTHONPATH="/app/src:$PYTHONPATH"

# Default entrypoint — overridden per stage by Step Functions ContainerOverrides
ENTRYPOINT ["python3.11"]
CMD ["-m", "data_engine", "--help"]
