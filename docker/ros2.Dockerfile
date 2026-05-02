# syntax=docker/dockerfile:1.6
# FR-7.3 — ROS 2 worker image: Ubuntu 24.04 + ROS 2 Jazzy Jalisco (TRD §5)
# Multi-stage: builder has full ros-jazzy-desktop + build tools; runtime uses
# ros-jazzy-ros-base to stay well under the 3 GB target.

# ── stage 1: builder — full desktop + colcon + rosdep ────────────────────────
FROM ubuntu:24.04 AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG ROS_DISTRO=jazzy

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Add the ROS 2 apt repository.
# The key is fetched directly (no pipe) to satisfy hadolint DL4006.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
        lsb-release \
    && mkdir -p /usr/share/keyrings \
    && curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu noble main" \
        > /etc/apt/sources.list.d/ros2.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-jazzy-desktop \
        ros-dev-tools \
        python3-colcon-common-extensions \
        python3-rosdep \
    && rosdep init \
    && rosdep update \
    && rm -rf /var/lib/apt/lists/*

# Workspace directory for future colcon builds (T2.x tasks).
WORKDIR /ws

# ── stage 2: runtime — ros-base + colcon, no build tools ─────────────────────
FROM ubuntu:24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive
ARG ROS_DISTRO=jazzy
LABEL org.opencontainers.image.title="rct-ros2" \
      org.opencontainers.image.description="Raleigh Commute Digital Twin — ROS 2 Jazzy worker (TRD §5)"

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Add the ROS 2 apt repository (same key as builder).
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && mkdir -p /usr/share/keyrings \
    && curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
        -o /usr/share/keyrings/ros-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu noble main" \
        > /etc/apt/sources.list.d/ros2.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-jazzy-ros-base \
        ros-jazzy-diagnostic-msgs \
        ros-jazzy-nav-msgs \
        ros-jazzy-sensor-msgs \
        python3-colcon-common-extensions \
        build-essential \
        cmake \
        libeigen3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy colcon install artifacts produced by the builder (initially empty).
COPY --from=builder /ws /ws

COPY --chmod=755 docker/ros2_entrypoint.sh /ros2_entrypoint.sh

WORKDIR /workspace
VOLUME ["/workspace", "/data", "/out"]

ENTRYPOINT ["/ros2_entrypoint.sh"]
CMD ["bash"]
