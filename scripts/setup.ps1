$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Assert-NativeSuccess {
  param([Parameter(Mandatory = $true)][string]$Step)
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
        Assert-NativeSuccess "Virtual environment creation"
        return
      }
    }
  }

  if (Get-Command python -ErrorAction SilentlyContinue) {
    $VersionOk = & python -c "import sys; print(int((3,12) <= sys.version_info[:2] < (3,14)))"
    Assert-NativeSuccess "Python version check"
    if ($VersionOk -eq "1") {
      Write-Host "Creating .venv with python..."
      & python -m venv .venv
      Assert-NativeSuccess "Virtual environment creation"
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
Assert-NativeSuccess "pip upgrade"

& ".\.venv\Scripts\python.exe" -m pip install -e ".\backend[dev]"
Assert-NativeSuccess "Backend dependency installation"

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
}

& ".\.venv\Scripts\python.exe" -c "from app.config import get_settings; settings = get_settings(); settings.ensure_directories(); print('Configuration OK')"
Assert-NativeSuccess "Configuration validation"

Push-Location frontend
try {
  npm ci
  Assert-NativeSuccess "Frontend dependency installation"
}
finally {
  Pop-Location
}

& ".\.venv\Scripts\python.exe" -m alembic -c backend\alembic.ini upgrade head
Assert-NativeSuccess "Database migration"

Write-Host "Setup complete." -ForegroundColor Green
