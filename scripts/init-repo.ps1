$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
if (-not (Test-Path ".git")) {
  git init
  git add .
  git commit -m "chore: bootstrap M0 engineering scaffold"
  Write-Host "Git repository initialized." -ForegroundColor Green
} else {
  Write-Host "Git repository already exists."
}
