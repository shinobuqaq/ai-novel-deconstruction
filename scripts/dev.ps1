$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Python' -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Python' -m app.worker"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\frontend'; npm run dev"

Write-Host "Started API, Worker and Frontend in three PowerShell windows." -ForegroundColor Green
