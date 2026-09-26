#!/usr/bin/env bash
# One-click launcher: FastAPI backend (127.0.0.1:8765) + Vite dev server.
# Usage: ./start_gui.sh [--skip-install]
set -euo pipefail

SKIP_INSTALL=false
if [[ "${1:-}" == "--skip-install" ]]; then
  SKIP_INSTALL=true
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Python environment check (do not silently create it)
if [[ ! -d "$ROOT/.venv" ]]; then
  echo "[start_gui] .venv not found. Run first: uv venv; uv sync --extra dev" >&2
  exit 1
fi

# 2. Backend
echo "[start_gui] Starting FastAPI backend on 127.0.0.1:8765 ..."
(cd "$ROOT" && uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765) &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
  echo "[start_gui] Backend stopped."
}
trap cleanup EXIT INT TERM

# 3. Frontend deps
cd "$ROOT/gui"
if [[ "$SKIP_INSTALL" == false && ! -d node_modules ]]; then
  echo "[start_gui] Installing gui dependencies (npm install) ..."
  npm install
fi

# 4. Vite dev server (foreground; Ctrl+C stops both)
echo "[start_gui] Starting Vite dev server (Ctrl+C to stop both) ..."
npm run dev
