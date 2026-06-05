# syntax=docker/dockerfile:1.6
# Valhalla routing engine for ECS Fargate (rct-valhalla service)
# Tiles are downloaded from S3 on startup to keep the image small.
# S3_BUCKET env var must be set (injected by ECS task definition).

FROM ghcr.io/gis-ops/docker-valhalla/valhalla:3.5.1

# Install AWS CLI v2 dependencies (awscli package from apt is v1, sufficient here)
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends awscli \
    && rm -rf /var/lib/apt/lists/*

# ECS-compatible Valhalla config (paths point to /data instead of /custom_files)
COPY docker/valhalla_ecs.json /valhalla.json

# Startup: download tiles from S3, extract, start service
COPY docker/valhalla-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8002

ENTRYPOINT ["/entrypoint.sh"]
