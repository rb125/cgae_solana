#!/usr/bin/env bash
set -euo pipefail

# Hosted framework endpoints provided by the user.
export CDCT_API_URL="https://cdct-framework.vercel.app"
export DDFT_API_URL="https://ddft-framework.vercel.app"
export EECT_API_URL="https://eect-framework.vercel.app"

if [[ "${1:-}" == "--live" ]]; then
  exec python3 -m simulation.live_runner --live
fi

ROUNDS="${1:-10}"
exec python3 -m simulation.live_runner --rounds "${ROUNDS}"
