<#
Swap the Streamlit theme and restart the server in one step.

    .\theme.ps1 dracula           # run preview_ui.py (no API calls, free)
    .\theme.ps1 candlelight -App  # run the real game (app.py)
    .\theme.ps1                   # list available themes

Streamlit reads .streamlit/config.toml only once, at process start.
Pressing R in the browser reruns the script but keeps the cached theme,
so switching themes requires restarting the server.

Messages are in English on purpose: Windows PowerShell 5.1 reads .ps1 as
ANSI unless the file has a UTF-8 BOM, and mojibake can break parsing.
#>
param(
    [string]$Name,
    [switch]$App,
    [int]$Port = 8501
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$themeDir = Join-Path $root "themes"

$available = @(Get-ChildItem $themeDir -Filter *.toml | ForEach-Object { $_.BaseName })

if (-not $Name) {
    Write-Host "Available themes:" -ForegroundColor Cyan
    $available | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Usage: .\theme.ps1 <name> [-App] [-Port 8501]"
    exit 0
}

if ($available -notcontains $Name) {
    Write-Host "No theme named '$Name'. Available: $($available -join ', ')" -ForegroundColor Red
    exit 1
}

# 1) Free the port. A leftover server is what silently blocks theme changes.
$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($procId in @($listeners.OwningProcess | Select-Object -Unique)) {
    Write-Host "Stopping process on port $Port (PID $procId)" -ForegroundColor DarkGray
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
}

# Also clear zombie streamlit processes that failed to bind the port.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like "*streamlit run*" } |
    ForEach-Object {
        Write-Host "Stopping leftover streamlit (PID $($_.ProcessId))" -ForegroundColor DarkGray
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 2

# 2) Copy the theme using absolute paths, so the current directory does not matter.
$configDir = Join-Path $root ".streamlit"
if (-not (Test-Path $configDir)) { New-Item -ItemType Directory -Force $configDir | Out-Null }
Copy-Item (Join-Path $themeDir "$Name.toml") (Join-Path $configDir "config.toml") -Force
Write-Host "Theme applied: $Name" -ForegroundColor Green

# 3) Make sure the API key reaches the child process. `setx` only affects new
#    shells, so a session started before it was set has an empty variable.
if (-not $env:ANTHROPIC_API_KEY) {
    $userKey = [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")
    if ($userKey) {
        $env:ANTHROPIC_API_KEY = $userKey
        Write-Host "Loaded ANTHROPIC_API_KEY from the user environment" -ForegroundColor DarkGray
    }
    elseif ($App) {
        Write-Host "Warning: ANTHROPIC_API_KEY is not set - app.py will show a key error." -ForegroundColor Yellow
    }
}

# 4) Restart the server. Absolute target path avoids the streamlit_app.py default.
$targetName = if ($App) { "app.py" } else { "preview_ui.py" }
$targetPath = Join-Path $root $targetName
if (-not (Test-Path $targetPath)) {
    Write-Host "Target not found: $targetPath" -ForegroundColor Red
    exit 1
}

Write-Host "Running $targetName on port $Port" -ForegroundColor Green
Write-Host "Open http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
Push-Location $root
try {
    & python -m streamlit run $targetPath --server.port $Port
}
finally {
    Pop-Location
}
