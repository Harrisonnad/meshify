<#
.SYNOPSIS
    One-command bring-up for all five services (master plan section 7: "You will forget the
    port assignments within two weeks.").

.DESCRIPTION
    Launches imggen (:8101) and meshgen (:8102) inside WSL2 Ubuntu, the blender worker (:8104)
    and orchestrator (:8100) on native Windows, and the UI dev server (:5173) - each in its
    own console window so logs stay visible and any one can be closed/Ctrl+C'd independently.
    Then polls the orchestrator's /health until all three GPU/CPU workers report "ok" (or
    times out), so you know the moment it's actually usable, not just "processes started."

.NOTES
    Assumes the one-time setup in docs/COMFYUI_SETUP.md, docs/MESH_GEN.md, and
    docs/ORCHESTRATOR.md is already done (WSL venvs at ~/comfyui and ~/triposr, orchestrator
    and ui npm installs, .venv-stub for the blender worker). This script starts things; it
    doesn't install them.

    Verified from an interactive PowerShell/Windows Terminal session: all 5 windows open and
    all 3 workers report healthy. NOT fully verified from an automated/sandboxed invocation
    (e.g. run via a CI runner or an agent's tool session without a real interactive desktop) -
    in that context the two WSL launches (imggen, meshgen) may not come up even though the
    three native-Windows ones (blender, orchestrator, ui) do, which looks like a WSL
    interactive-session requirement rather than a bug in this script. If the health poll below
    times out on imggen/meshgen specifically, check whether you're in that kind of
    non-interactive context before assuming the script is broken.
#>

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# Windows path -> WSL mount path, e.g. C:\Users\x\proj -> /mnt/c/Users/x/proj
$drive = $repoRoot.Substring(0, 1).ToLower()
$rest = $repoRoot.Substring(2) -replace '\\', '/'
$wslRepoRoot = "/mnt/$drive$rest"

Write-Host "Starting Local 3D Asset Forge - 5 services, each in its own window..." -ForegroundColor Cyan

# Start-Process wsl -ArgumentList @(...) directly does not reliably keep the WSL process
# alive here - wrapping it in a PowerShell host window (same pattern as the three native
# processes below) is what actually works.
$imggenCmd = "wsl -d Ubuntu -- bash -c `"cd $wslRepoRoot/workers/imggen && source ~/comfyui/.venv/bin/activate && python server.py --port 8101`""
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $imggenCmd)

$meshgenCmd = "wsl -d Ubuntu -- bash -c `"cd $wslRepoRoot/workers/meshgen && source ~/triposr/.venv/bin/activate && python server.py --port 8102`""
Start-Process powershell -ArgumentList @("-NoExit", "-Command", $meshgenCmd)

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repoRoot\workers\blender'; & '$repoRoot\.venv-stub\Scripts\python.exe' server.py --port 8104"
)

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repoRoot\orchestrator'; npm start"
)

Start-Process powershell -ArgumentList @(
    "-NoExit", "-Command",
    "cd '$repoRoot\ui'; npm run dev"
)

Write-Host "Waiting for workers to come up (this can take ~15-20s for a cold ComfyUI start)..."
$deadline = (Get-Date).AddSeconds(60)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8100/health" -TimeoutSec 3
        $statuses = $health.workers.PSObject.Properties | ForEach-Object { "$($_.Name): $($_.Value.status)" }
        Write-Host ("  " + ($statuses -join " | "))
        if (($health.workers.PSObject.Properties | Where-Object { $_.Value.status -ne "ok" } | Measure-Object).Count -eq 0) {
            $ready = $true
            break
        }
    } catch {
        Write-Host "  orchestrator not up yet..."
    }
}

if ($ready) {
    Write-Host ""
    Write-Host "All services healthy." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "Timed out waiting for all workers to report healthy - check the individual windows for errors." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  UI:           http://localhost:5173"
Write-Host "  Orchestrator: http://127.0.0.1:8100  (GET /health, /jobs)"
Write-Host "  imggen:       http://127.0.0.1:8101"
Write-Host "  meshgen:      http://127.0.0.1:8102"
Write-Host "  blender:      http://127.0.0.1:8104"
