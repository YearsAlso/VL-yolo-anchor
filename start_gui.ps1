# One-click launcher: FastAPI backend (127.0.0.1:8765) + Vite dev server.
# Usage: ./start_gui.ps1 [-SkipInstall]

param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }

# 1. Python environment check (do not silently create it)
if (-not (Test-Path (Join-Path $root ".venv"))) {
    Write-Host "[start_gui] .venv not found. Run first: uv venv; uv sync --extra dev" -ForegroundColor Red
    exit 1
}

# 2. Backend
Write-Host "[start_gui] Starting FastAPI backend on 127.0.0.1:8765 ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath "uv" -ArgumentList "run", "uvicorn", "src.api_server:app", `
    "--host", "127.0.0.1", "--port", "8765" -WorkingDirectory $root -PassThru -NoNewWindow

# 3. Frontend deps
Push-Location (Join-Path $root "gui")
try {
    if (-not $SkipInstall -and -not (Test-Path "node_modules")) {
        Write-Host "[start_gui] Installing gui dependencies (npm install) ..." -ForegroundColor Cyan
        npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[start_gui] npm install failed." -ForegroundColor Red
            Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
            exit 1
        }
    }

    # 4. Vite dev server (foreground; Ctrl+C stops it)
    try {
        Write-Host "[start_gui] Starting Vite dev server (Ctrl+C to stop both) ..." -ForegroundColor Cyan
        npm run dev
    }
    finally {
        if (-not $backend.HasExited) {
            Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue
            Write-Host "[start_gui] Backend stopped." -ForegroundColor Yellow
        }
    }
}
finally {
    Pop-Location
}
