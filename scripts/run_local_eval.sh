#!/usr/bin/env bash
# Local pipeline runner — delegates to run_full_pipeline.sh.
# Usage: ./scripts/run_local_eval.sh [TRACE]   (default: day2)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/run_full_pipeline.sh" "$@"
