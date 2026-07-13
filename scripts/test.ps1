$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
& ".\.venv\Scripts\python.exe" -m pytest backend\tests
