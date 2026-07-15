$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

& $Python -m pytest backend\tests
if ($LASTEXITCODE -ne 0) {
  throw "Backend tests failed with exit code $LASTEXITCODE."
}

Write-Host "Backend tests passed." -ForegroundColor Green
