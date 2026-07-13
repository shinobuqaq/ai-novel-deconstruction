$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
& ".\.venv\Scripts\python.exe" -m alembic -c backend\alembic.ini upgrade head
Write-Host "Database migrated." -ForegroundColor Green
