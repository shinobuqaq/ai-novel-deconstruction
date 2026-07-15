$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

& $Python -m alembic -c backend\alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) {
  throw "Database migration failed with exit code $LASTEXITCODE."
}

Write-Host "Database migrated." -ForegroundColor Green
