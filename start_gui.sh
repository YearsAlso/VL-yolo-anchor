#!/usr/bin/env bash
# Launches the FastAPI backend + Vite dev server for the GUI (Linux/macOS)
set -euo pipefail

echo "Starting FastAPI backend on 127.0.0.1:8765 ..."
uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765 &
BACKEND_PID=$!

cd gui
if [ ! -d node_modules ]; then
    echo "Installing GUI dependencies (npm install) ..."
    npm install
fi
npm run dev

kill "$BACKEND_PID" 2>/dev/null || true
