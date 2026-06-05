#!/bin/bash
# Valhalla ECS entrypoint — downloads tiles from S3 then starts the service.
set -euo pipefail

DATA_DIR="/data"
TILES_TAR="${DATA_DIR}/valhalla_tiles.tar"
CONFIG="/valhalla.json"

mkdir -p "${DATA_DIR}"

# Download tiles from S3 if S3_BUCKET is set (always in ECS)
if [ -n "${S3_BUCKET:-}" ]; then
    echo "[valhalla-entrypoint] Downloading tiles from s3://${S3_BUCKET}/valhalla/valhalla_tiles.tar"
    aws s3 cp "s3://${S3_BUCKET}/valhalla/valhalla_tiles.tar" "${TILES_TAR}"
    echo "[valhalla-entrypoint] Download complete."
fi

# Extract if tile directory doesn't already exist
if [ ! -d "${DATA_DIR}/valhalla_tiles" ] && [ -f "${TILES_TAR}" ]; then
    echo "[valhalla-entrypoint] Extracting tiles..."
    tar -xf "${TILES_TAR}" -C "${DATA_DIR}"
    echo "[valhalla-entrypoint] Extraction complete."
fi

echo "[valhalla-entrypoint] Starting valhalla_service on port 8002..."
exec valhalla_service "${CONFIG}" 1
