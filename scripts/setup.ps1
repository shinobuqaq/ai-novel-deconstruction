$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Assert-NativeSuccess {
  param([string]$Step)
  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE."
  }
}

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
        Assert-NativeSuccess "Creating Python virtual environment"
        return
      }
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $VersionOk = & python -c "import sys; print(int((3,12) <= sys.version_info[:2] < (3,14)))"
    if ($VersionOk -eq "1") {
      Write-Host "Creating .venv with python..."
      & python -m venv .venv
      Assert-NativeSuccess "Creating Python virtual environment"
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
Assert-NativeSuccess "Upgrading pip"

& ".\.venv\Scripts\python.exe" -m pip install -e ".\backend[dev]"
Assert-NativeSuccess "Installing backend dependencies"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

Push-Location frontend
try {
  npm ci
  Assert-NativeSuccess "Installing frontend dependencies"
}
finally {
  Pop-Location
}

& ".\.venv\Scripts\python.exe" -m alembic -c backend\alembic.ini upgrade head
Assert-NativeSuccess "Running database migrations"

Write-Host "Setup complete." -ForegroundColor Green
