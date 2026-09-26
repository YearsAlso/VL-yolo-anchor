# Launches the FastAPI backend + Vite dev server for the GUI (Windows PowerShell)

$ErrorActionPreference = "Stop"

Write-Host "Starting FastAPI backend on 127.0.0.1:8765 ..."
Start-Process powershell -ArgumentList "-NoExit", "-Command", "uv run uvicorn src.api_server:app --host 127.0.0.1 --port 8765"

Write-Host "Starting Vite dev server (gui/) ..."
Push-Location gui
if (-not (Test-Path node_modules)) {
    Write-Host "Installing GUI dependencies (npm install) ..."
    npm install
}
npm run dev
Pop-Location
