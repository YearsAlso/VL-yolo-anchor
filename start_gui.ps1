# One-click launcher: FastAPI backend (127.0.0.1:8765) + Vite dev server.
# Usage: ./start_gui.ps1 [-SkipInstall]

param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
$backendExe = Join-Path $root ".venv\Scripts\python.exe"

# 1. Python environment check (do not silently create it)
if (-not (Test-Path $backendExe)) {
    Write-Host "[start_gui] .venv not found. Run first: uv venv; uv sync --extra dev" -ForegroundColor Red
    exit 1
}

# 2. Port availability check (fail fast instead of leaving a blank GUI)
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "[start_gui] Port 8765 is already in use. Stop the other process first." -ForegroundColor Red
    exit 1
}

# 3. Backend: launch the venv python directly so the tracked PID is the real
#    uvicorn server (uv.exe would leave an orphaned child python.exe behind).
Write-Host "[start_gui] Starting FastAPI backend on 127.0.0.1:8765 ..." -ForegroundColor Cyan
$backend = Start-Process -FilePath $backendExe -ArgumentList `
    "-m", "uvicorn", "src.api_server:app", "--host", "127.0.0.1", "--port", "8765" `
    -WorkingDirectory $root -PassThru -NoNewWindow

function Stop-Backend {
    if ($backend -and -not $backend.HasExited) {
        # Kill the whole tree so uvicorn worker children are cleaned up too.
        & taskkill /PID $backend.Id /T /F 2>$null | Out-Null
        Write-Host "[start_gui] Backend stopped." -ForegroundColor Yellow
    }
}

try {
    # 4. Health check: wait for the backend to answer, abort if it dies early.
    #    /api/health is the one endpoint that stays reachable when API auth is
    #    on; probing /api/tasks would report "unhealthy" on a correctly
    #    configured deployment that simply requires a bearer token.
    $healthy = $false
    for ($i = 0; $i -lt 20; $i++) {
        if ($backend.HasExited) { break }
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 1 -UseBasicParsing | Out-Null
            $healthy = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        Write-Host "[start_gui] Backend failed to become healthy on 8765." -ForegroundColor Red
        Stop-Backend
        exit 1
    }

    # 4b. Self-check. Advisory only: a misconfigured platform still starts (the
    #     GUI needs to be reachable precisely when the configuration is wrong),
    #     but the user is told loudly, and pointed at the settings page.
    Write-Host "[start_gui] Running platform self-check (run.py doctor) ..." -ForegroundColor Cyan
    $prevEap = $ErrorActionPreference
    # Native stderr -> stdout under "Stop" turns doctor's warnings into a
    # terminating error in Windows PowerShell 5.1.
    $ErrorActionPreference = "Continue"
    Push-Location $root
    try {
        $doctorOut = & $backendExe "run.py" doctor 2>&1 | Out-String
        $doctorCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
        $ErrorActionPreference = $prevEap
    }
    Write-Host $doctorOut
    if ($doctorCode -ne 0 -or $doctorOut -match '\[(WARN|FAIL)\]') {
        Write-Host ""
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
        Write-Host "[start_gui] WARNING: the self-check found problems (see above)." -ForegroundColor Yellow
        Write-Host "[start_gui] The GUI still starts, but the platform may be producing" -ForegroundColor Yellow
        Write-Host "[start_gui] STUB (fake) labels instead of real model output." -ForegroundColor Yellow
        Write-Host "[start_gui] Open the 'settings' tab to fix the model configuration," -ForegroundColor Yellow
        Write-Host "[start_gui] or run: .venv\Scripts\python.exe run.py doctor --deep" -ForegroundColor Yellow
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Yellow
        Write-Host ""
    }

    # 5. Frontend deps
    Push-Location (Join-Path $root "gui")
    try {
        if (-not $SkipInstall -and -not (Test-Path "node_modules")) {
            Write-Host "[start_gui] Installing gui dependencies (npm install) ..." -ForegroundColor Cyan
            npm install
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[start_gui] npm install failed." -ForegroundColor Red
                exit 1
            }
        }

        # 6. Vite dev server (foreground; Ctrl+C stops both via finally)
        Write-Host "[start_gui] Starting Vite dev server (Ctrl+C to stop both) ..." -ForegroundColor Cyan
        npm run dev
        $script:exitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    Stop-Backend
}

exit $(if ($script:exitCode) { $script:exitCode } else { 0 })
