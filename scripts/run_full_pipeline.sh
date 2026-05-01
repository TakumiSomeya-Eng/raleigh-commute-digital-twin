#!/usr/bin/env bash
# T5.5 -- Full end-to-end pipeline: fresh clone → report.html
#
# Usage:
#   ./scripts/run_full_pipeline.sh [TRACE]          # default: day2
#   ./scripts/run_full_pipeline.sh day1
#   FILTER=ukf ./scripts/run_full_pipeline.sh day2
#
# Prerequisites:
#   - Docker running (docker compose available)
#   - Raw data directory present (see DATA_DIR below)
#   - Python env with deps installed (pip install -e .[dev])
#
# Exit codes:
#   0  success — out/TRACE/report.html produced
#   1  prerequisites not met
#   2  Docker / Valhalla startup failure
#   3  pipeline stage failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TRACE="${1:-day2}"
FILTER="${FILTER:-ekf}"

# Resolve project root (one level up from scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Raw data directories (must match Makefile TRACE_* variables)
DATA_DIR_day1="${PROJECT_ROOT}/../../../Data/Saint_Marys_Street-2026-04-16_13-27-45"
DATA_DIR_day2="${PROJECT_ROOT}/../../../Data/Saint_Marys_Street-2026-04-17_13-20-03"

VALHALLA_URL="${VALHALLA_URL:-http://localhost:8002}"
VALHALLA_WAIT_SEC="${VALHALLA_WAIT_SEC:-300}"   # max 5 min

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

_ts() {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
}

_log() {
    local level="${1}"; shift
    printf '[%s] [T5.5 pipeline] %-5s %s\n' "$(_ts)" "${level}" "$*"
}

_info()  { _log INFO  "$*"; }
_warn()  { _log WARN  "$*"; }
_error() { _log ERROR "$*" >&2; }

# ---------------------------------------------------------------------------
# Stage timer
# ---------------------------------------------------------------------------

declare -A _STAGE_ELAPSED

_stage_start() {
    _STAGE_START_TS="${SECONDS}"
    _info "=== stage: $* ==="
}

_stage_done() {
    local name="$1"
    local elapsed=$(( SECONDS - _STAGE_START_TS ))
    _STAGE_ELAPSED["${name}"]="${elapsed}"
    _info "--- stage ${name} done in ${elapsed}s ---"
}

_print_summary() {
    local total=$(( SECONDS - _PIPELINE_START_TS ))
    printf '\n'
    _info "========================================"
    _info "Pipeline complete for TRACE=${TRACE}"
    _info "========================================"
    for stage in "${_STAGE_ORDER[@]}"; do
        if [[ -n "${_STAGE_ELAPSED[${stage}]+x}" ]]; then
            printf '[%s] [T5.5 pipeline] INFO    %-18s %3ds\n' "$(_ts)" "${stage}" "${_STAGE_ELAPSED[${stage}]}"
        fi
    done
    _info "----------------------------------------"
    _info "Total elapsed:     ${total}s  ($(( total / 60 ))m$(( total % 60 ))s)"
    local report_path="${PROJECT_ROOT}/out/${TRACE}/report.html"
    if [[ -f "${report_path}" ]]; then
        local size_kb
        size_kb=$(du -k "${report_path}" | awk '{print $1}')
        _info "Output:            out/${TRACE}/report.html  (${size_kb} KB)"
    fi
    local index_path="${PROJECT_ROOT}/out/reports/index.html"
    if [[ -f "${index_path}" ]]; then
        _info "Index:             out/reports/index.html"
    fi
    _info "========================================"
}

_STAGE_ORDER=(data fuse eval-gt eval-rmse eval-compare ideal-match ref speed traj score report-render report-index)

# ---------------------------------------------------------------------------
# Prerequisites check
# ---------------------------------------------------------------------------

_check_prerequisites() {
    _info "Checking prerequisites..."

    # Docker
    if ! command -v docker &>/dev/null; then
        _error "docker not found in PATH. Install Docker Desktop and retry."
        exit 1
    fi
    if ! docker info &>/dev/null; then
        _error "Docker daemon not running. Start Docker Desktop and retry."
        exit 1
    fi
    _info "  docker: OK"

    # docker compose (plugin or standalone)
    if ! docker compose version &>/dev/null 2>&1 && ! docker-compose version &>/dev/null 2>&1; then
        _error "docker compose not available. Install Docker Compose v2 and retry."
        exit 1
    fi
    _info "  docker compose: OK"

    # data directory
    local data_var="DATA_DIR_${TRACE}"
    local data_dir="${!data_var:-}"
    if [[ -z "${data_dir}" ]]; then
        _error "Unknown TRACE='${TRACE}'. Supported: day1, day2"
        exit 1
    fi
    if [[ ! -d "${data_dir}" ]]; then
        _error "Data directory not found: ${data_dir}"
        _error "Download the raw Sensor Logger CSV files and place them there."
        exit 1
    fi
    _info "  data dir (${TRACE}): ${data_dir}"

    # Python
    if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
        _error "Python not found in PATH."
        exit 1
    fi
    _info "  python: OK"

    # pyproject extras
    if ! python3 -c "import reporting" &>/dev/null 2>&1 \
       && ! python -c "import reporting" &>/dev/null 2>&1; then
        _warn "  reporting package not importable; trying with PYTHONPATH=src"
    fi

    _info "Prerequisites OK."
}

# ---------------------------------------------------------------------------
# Valhalla startup
# ---------------------------------------------------------------------------

_compose() {
    # Prefer docker compose (v2 plugin) over docker-compose (v1 standalone)
    if docker compose version &>/dev/null 2>&1; then
        docker compose -f "${PROJECT_ROOT}/docker-compose.yml" "$@"
    else
        docker-compose -f "${PROJECT_ROOT}/docker-compose.yml" "$@"
    fi
}

_wait_for_valhalla() {
    _info "Waiting for Valhalla at ${VALHALLA_URL} (max ${VALHALLA_WAIT_SEC}s)..."
    local waited=0
    local interval=10
    while true; do
        if curl -sf "${VALHALLA_URL}/status" &>/dev/null; then
            _info "Valhalla is healthy (${waited}s elapsed)."
            return 0
        fi
        if (( waited >= VALHALLA_WAIT_SEC )); then
            _error "Valhalla did not become healthy within ${VALHALLA_WAIT_SEC}s."
            _error "Check: docker logs \$(docker compose ps -q valhalla)"
            return 1
        fi
        sleep "${interval}"
        waited=$(( waited + interval ))
        _info "  still waiting... (${waited}s)"
    done
}

_start_valhalla() {
    _stage_start "valhalla-start"

    # Check if already healthy
    if curl -sf "${VALHALLA_URL}/status" &>/dev/null; then
        _info "Valhalla already running and healthy — skipping docker compose up."
        _STAGE_ELAPSED["valhalla-start"]=0
        return 0
    fi

    _info "Starting Valhalla service via docker compose..."
    _compose up -d valhalla

    if ! _wait_for_valhalla; then
        exit 2
    fi
    _stage_done "valhalla-start"
}

# ---------------------------------------------------------------------------
# Make wrapper (logs + timing)
# ---------------------------------------------------------------------------

_make() {
    make -C "${PROJECT_ROOT}" "$@"
}

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_PIPELINE_START_TS="${SECONDS}"

_info "========================================"
_info "Raleigh Commute Digital Twin — Full Pipeline"
_info "TRACE=${TRACE}  FILTER=${FILTER}"
_info "Project: ${PROJECT_ROOT}"
_info "========================================"

_check_prerequisites
_start_valhalla

# --- Stage: data ingest ---
_stage_start "data"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/aligned_100hz.parquet" ]]; then
    _info "aligned_100hz.parquet already exists — skipping data ingest."
    _STAGE_ELAPSED["data"]=0
else
    _make data TRACE="${TRACE}"
    _stage_done "data"
fi

# --- Stage: sensor fusion ---
_stage_start "fuse"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/fused_${FILTER}.parquet" ]]; then
    _info "fused_${FILTER}.parquet already exists — skipping fusion."
    _STAGE_ELAPSED["fuse"]=0
else
    _make bag  TRACE="${TRACE}"
    _make fuse TRACE="${TRACE}" FILTER="${FILTER}"
    _stage_done "fuse"
fi

# --- Stage: evaluation (ground-truth smoothing) ---
_stage_start "eval-gt"
_make eval TRACE="${TRACE}" FILTER="${FILTER}" STAGE=gt
_stage_done "eval-gt"

# --- Stage: evaluation (RMSE) ---
_stage_start "eval-rmse"
_make eval TRACE="${TRACE}" FILTER="${FILTER}" STAGE=rmse
_stage_done "eval-rmse"

# --- Stage: evaluation (compare) ---
_stage_start "eval-compare"
_make eval TRACE="${TRACE}" FILTER="${FILTER}" STAGE=compare
_stage_done "eval-compare"

# --- Stage: ideal driver — map-match ---
_stage_start "ideal-match"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/matched_route.json" ]]; then
    _info "matched_route.json already exists — skipping map-match."
    _STAGE_ELAPSED["ideal-match"]=0
else
    _make ideal TRACE="${TRACE}" VALHALLA_URL="${VALHALLA_URL}"
    _stage_done "ideal-match"
fi

# --- Stage: ideal driver — road reference ---
_stage_start "ref"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/road_ref.parquet" ]]; then
    _info "road_ref.parquet already exists — skipping ref."
    _STAGE_ELAPSED["ref"]=0
else
    _make ref TRACE="${TRACE}"
    _stage_done "ref"
fi

# --- Stage: ideal driver — speed profile ---
_stage_start "speed"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/ideal_speed.parquet" ]]; then
    _info "ideal_speed.parquet already exists — skipping speed."
    _STAGE_ELAPSED["speed"]=0
else
    _make speed TRACE="${TRACE}"
    _stage_done "speed"
fi

# --- Stage: ideal driver — trajectory synthesis ---
_stage_start "traj"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/ideal_trajectory.parquet" ]]; then
    _info "ideal_trajectory.parquet already exists — skipping traj."
    _STAGE_ELAPSED["traj"]=0
else
    _make traj TRACE="${TRACE}"
    _stage_done "traj"
fi

# --- Stage: scoring ---
_stage_start "score"
if [[ -f "${PROJECT_ROOT}/out/${TRACE}/score.json" ]]; then
    _info "score.json already exists — skipping score."
    _STAGE_ELAPSED["score"]=0
else
    _make score TRACE="${TRACE}" FILTER="${FILTER}"
    _stage_done "score"
fi

# --- Stage: report render ---
_stage_start "report-render"
_make report TRACE="${TRACE}"
_stage_done "report-render"

# report-index is invoked by 'make report' — mark done with 0 extra cost
_STAGE_ELAPSED["report-index"]=0

# ---------------------------------------------------------------------------
# Verify output
# ---------------------------------------------------------------------------

REPORT_PATH="${PROJECT_ROOT}/out/${TRACE}/report.html"
INDEX_PATH="${PROJECT_ROOT}/out/reports/index.html"

if [[ ! -f "${REPORT_PATH}" ]]; then
    _error "Expected output not found: out/${TRACE}/report.html"
    exit 3
fi
if [[ ! -f "${INDEX_PATH}" ]]; then
    _warn "Index page not found: out/reports/index.html"
fi

_print_summary
