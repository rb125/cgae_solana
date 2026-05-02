#!/usr/bin/env bash
set -euo pipefail

# Framework API endpoints — override via env vars if needed.
export CDCT_API_URL="${CDCT_API_URL:-http://localhost:8001}"
export DDFT_API_URL="${DDFT_API_URL:-http://localhost:8002}"
export EECT_API_URL="${EECT_API_URL:-http://localhost:8003}"

if [[ "${1:-}" == "--live" ]]; then
  exec python3 -m server.live_runner --live
fi

ROUNDS="${1:-10}"
exec python3 -m server.live_runner --rounds "${ROUNDS}"
