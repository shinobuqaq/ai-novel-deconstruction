param(
  [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
  throw "Missing .venv. Run scripts\setup.ps1 first."
}

function Invoke-LoggedPython {
  param(
    [string[]]$Arguments,
    [string]$LogPath,
    [string]$FailureLabel
  )

  $StdoutPath = "$LogPath.stdout"
  $StderrPath = "$LogPath.stderr"
  $Process = Start-Process -FilePath $Python `
    -ArgumentList $Arguments `
    -WorkingDirectory $Root `
    -NoNewWindow `
    -RedirectStandardOutput $StdoutPath `
    -RedirectStandardError $StderrPath `
    -Wait `
    -PassThru

  $Lines = @()
  if (Test-Path $StdoutPath) {
    $Lines += Get-Content $StdoutPath
  }
  if (Test-Path $StderrPath) {
    $Lines += Get-Content $StderrPath
  }
  $Lines | Set-Content -Path $LogPath -Encoding UTF8
  $Lines | ForEach-Object { Write-Host $_ }

  if ($Process.ExitCode -ne 0) {
    throw "$FailureLabel failed with exit code $($Process.ExitCode). Log: $LogPath"
  }
}

if (-not $OutputRoot) {
  $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $OutputRoot = Join-Path $Root "workspace\diagnostics\m0-gate\$Stamp"
}
$RunDir = [System.IO.Path]::GetFullPath($OutputRoot)
$WorkspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $Root "workspace"))
if (-not $RunDir.StartsWith($WorkspaceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "OutputRoot must stay inside the repository workspace directory."
}
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

$Database = Join-Path $RunDir "gate.db"
$GateWorkspace = Join-Path $RunDir "workspace"
$Sentinel = Join-Path $RunDir "claimed.json"
$DatabaseUrlPath = $Database.Replace("\", "/")
$env:AND_DATABASE_URL = "sqlite:///$DatabaseUrlPath"
$env:AND_WORKSPACE_DIR = $GateWorkspace
$env:AND_WORKER_LEASE_SECONDS = "2"
$env:AND_WORKER_POLL_SECONDS = "0.1"
$env:AND_ARTIFACT_RECOVERY_STALE_SECONDS = "0"

$MigrationLog = Join-Path $RunDir "migration.log"
Invoke-LoggedPython `
  -Arguments @("-m", "alembic", "-c", "backend\alembic.ini", "upgrade", "head") `
  -LogPath $MigrationLog `
  -FailureLabel "Migration"

$TaskId = (& $Python -m app.m0_gate_probe seed).Trim()
if ($LASTEXITCODE -ne 0 -or -not $TaskId.StartsWith("tsk_")) {
  throw "Gate task seed failed. Output: $TaskId"
}

$CrashProcess = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "app.m0_gate_probe", "claim-and-hang", "--sentinel", $Sentinel) `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $RunDir "crash-probe.stdout.log") `
  -RedirectStandardError (Join-Path $RunDir "crash-probe.stderr.log") `
  -PassThru

try {
  $Claimed = $false
  for ($Attempt = 0; $Attempt -lt 100; $Attempt++) {
    if (Test-Path $Sentinel) {
      $Claimed = $true
      break
    }
    if ($CrashProcess.HasExited) {
      throw "Crash probe exited before claiming the task."
    }
    Start-Sleep -Milliseconds 100
  }
  if (-not $Claimed) {
    throw "Crash probe did not claim the task within 10 seconds."
  }

  Stop-Process -Id $CrashProcess.Id -Force
  $CrashProcess.WaitForExit()
}
finally {
  if (-not $CrashProcess.HasExited) {
    Stop-Process -Id $CrashProcess.Id -Force
  }
}

Start-Sleep -Seconds 3
$WorkerLog = Join-Path $RunDir "recovery-worker.log"
Invoke-LoggedPython `
  -Arguments @("-m", "app.worker", "--once") `
  -LogPath $WorkerLog `
  -FailureLabel "Recovery worker"

$TestLog = Join-Path $RunDir "reliability-tests.log"
Invoke-LoggedPython `
  -Arguments @("-m", "pytest", "backend\tests\reliability") `
  -LogPath $TestLog `
  -FailureLabel "Reliability tests"

$ResultPath = Join-Path $RunDir "result.json"
& $Python -m app.m0_gate_probe verify --task-id $TaskId |
  Tee-Object -FilePath $ResultPath
if ($LASTEXITCODE -ne 0) {
  throw "M0 hard-kill verification failed. Result: $ResultPath"
}

Write-Host "M0 reliability gate passed. Result: $ResultPath" -ForegroundColor Green
