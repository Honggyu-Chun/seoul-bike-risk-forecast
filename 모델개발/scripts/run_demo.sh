#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.cache/uv}"
# Installation is an explicit `uv sync --frozen` step. Starting the demo must
# preserve optional research packages used by concurrent training workers.
exec uv run --frozen --no-sync uvicorn bike_demo.api:app --host 0.0.0.0 --port "${BIKE_DEMO_PORT:-8000}"
