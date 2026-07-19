$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

# Keep the development frontend on the same backend as this script. An inherited
# VITE_API_URL can otherwise make the page silently call an older preview port.
$FrontendApiUrl = "http://127.0.0.1:8000"

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Python' -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Python' -m app.worker"
$FrontendCommand = "Set-Location '$Root\frontend'; `$env:VITE_API_URL='$FrontendApiUrl'; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $FrontendCommand

Write-Host "Started API, Worker and Frontend in three PowerShell windows. Frontend API: $FrontendApiUrl" -ForegroundColor Green
