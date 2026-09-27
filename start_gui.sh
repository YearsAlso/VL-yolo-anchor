#!/usr/bin/env bash
# One-click launcher: FastAPI backend (127.0.0.1:8765) + Vite dev server.
# Usage: ./start_gui.sh [--skip-install]
set -euo pipefail

SKIP_INSTALL=false
if [[ "${1:-}" == "--skip-install" ]]; then
  SKIP_INSTALL=true
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_EXE="$ROOT/.venv/bin/python"

# 1. Python environment check (do not silently create it)
if [[ ! -x "$BACKEND_EXE" ]]; then
  echo "[start_gui] .venv not found. Run first: uv venv; uv sync --extra dev" >&2
  exit 1
fi

# 2. Port availability check (fail fast instead of leaving a blank GUI)
if lsof -nP -iTCP:8765 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[start_gui] Port 8765 is already in use. Stop the other process first." >&2
  exit 1
fi

# 3. Backend: launch the venv python directly so the tracked PID is the real
#    uvicorn server (uv.exe would leave an orphaned child python.exe behind).
#    `exec` makes the background subshell become python, so $! is its PID.
echo "[start_gui] Starting FastAPI backend on 127.0.0.1:8765 ..."
( cd "$ROOT" && exec "$BACKEND_EXE" -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765 ) &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  echo "[start_gui] Backend stopped."
}
trap cleanup EXIT INT TERM

# 4. Health check: wait for the backend to answer, abort if it dies early.
#    /api/health is the one endpoint that stays reachable when API auth is on;
#    probing /api/tasks would report "unhealthy" on a correctly configured
#    deployment that simply requires a bearer token.
healthy=false
for _ in $(seq 1 20); do
  if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
    break
  fi
  if curl -fs --max-time 1 "http://127.0.0.1:8765/api/health" >/dev/null 2>&1; then
    healthy=true
    break
  fi
  sleep 0.5
done
if [[ "$healthy" == false ]]; then
  echo "[start_gui] Backend failed to become healthy on 8765." >&2
  exit 1
fi

# 4b. Self-check. Advisory only: a misconfigured platform still starts (the GUI
#     needs to be reachable precisely when the configuration is wrong), but the
#     user is told loudly, and pointed at the settings page that fixes it.
echo "[start_gui] Running platform self-check (run.py doctor) ..."
set +e
DOCTOR_OUT="$(cd "$ROOT" && "$BACKEND_EXE" run.py doctor 2>&1)"
DOCTOR_CODE=$?
set -e
printf '%s\n' "$DOCTOR_OUT"
if [[ "$DOCTOR_CODE" -ne 0 ]] || grep -q -e '\[WARN\]' -e '\[FAIL\]' <<<"$DOCTOR_OUT"; then
  echo "" >&2
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
  echo "[start_gui] WARNING: the self-check found problems (see above)." >&2
  echo "[start_gui] The GUI still starts, but the platform may be producing" >&2
  echo "[start_gui] STUB (fake) labels instead of real model output." >&2
  echo "[start_gui] Open the \"settings\" tab to fix the model configuration," >&2
  echo "[start_gui] or run: .venv/bin/python run.py doctor --deep" >&2
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" >&2
  echo "" >&2
fi

# 5. Frontend deps
cd "$ROOT/gui"
if [[ "$SKIP_INSTALL" == false && ! -d node_modules ]]; then
  echo "[start_gui] Installing gui dependencies (npm install) ..."
  npm install
fi

# 6. Vite dev server (foreground; Ctrl+C stops both via trap)
echo "[start_gui] Starting Vite dev server (Ctrl+C to stop both) ..."
set +e
npm run dev
EXIT_CODE=$?
set -e

exit "${EXIT_CODE}"
