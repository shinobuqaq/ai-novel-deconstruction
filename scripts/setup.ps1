$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function New-CompatibleVenv {
  if (Test-Path ".venv") {
    return
  }

  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($Version in @("3.13", "3.12")) {
      & py "-$Version" -c "import sys; print(sys.version)" *> $null
      if ($LASTEXITCODE -eq 0) {
        Write-Host "Creating .venv with Python $Version..."
        & py "-$Version" -m venv .venv
        return
      }
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $VersionOk = & python -c "import sys; print(int((3,12) <= sys.version_info[:2] < (3,14)))"
    if ($VersionOk -eq "1") {
      Write-Host "Creating .venv with python..."
      & python -m venv .venv
      return
    }
  }

  throw "Python 3.12 or 3.13 was not found. Install one of them and run this script again."
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw "npm was not found. Install Node.js 20 or newer and run this script again."
}

New-CompatibleVenv

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e ".\backend[dev]"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

Push-Location frontend
npm ci
Pop-Location

& ".\.venv\Scripts\python.exe" -m alembic -c backend\alembic.ini upgrade head
Write-Host "Setup complete." -ForegroundColor Green
