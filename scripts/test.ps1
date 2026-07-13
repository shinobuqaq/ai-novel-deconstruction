$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

& $Python -m pytest backend\tests
if ($LASTEXITCODE -ne 0) {
  throw "Tests failed with exit code $LASTEXITCODE."
}

Write-Host "Tests passed." -ForegroundColor Green
