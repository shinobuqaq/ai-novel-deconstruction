param(
  [int]$ApiPort = 8013,
  [int]$FrontendPort = 5175,
  [switch]$MockProvider,
  [int]$MockProviderPort = 8099
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Npm = (Get-Command npm.cmd -ErrorAction Stop).Source

if (-not (Test-Path $Python)) {
  throw "缺少项目 Python 环境。请先运行 scripts\setup.ps1。"
}

$Ports = @($ApiPort, $FrontendPort)
if ($MockProvider) {
  $Ports += $MockProviderPort
}
foreach ($Port in $Ports) {
  if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "端口 $Port 已被占用。请传入其他端口，现有服务不会被停止。"
  }
}

$PreviewRoot = Join-Path $Root "workspace\product-preview-$ApiPort"
$LogRoot = Join-Path $Root "workspace\logs\product-preview-$ApiPort"
New-Item -ItemType Directory -Force -Path $PreviewRoot, $LogRoot | Out-Null

$DatabasePath = (Join-Path $PreviewRoot "preview.db").Replace("\", "/")
$env:AND_DATABASE_URL = "sqlite:///$DatabasePath"
$env:AND_WORKSPACE_DIR = $PreviewRoot
$env:AND_CORS_ORIGINS = "[`"http://127.0.0.1:$FrontendPort`",`"http://localhost:$FrontendPort`"]"
$env:VITE_API_URL = "http://127.0.0.1:$ApiPort"

$MigrationLog = Join-Path $LogRoot "migration.log"
& $Python scripts\prepare_preview_database.py --database $DatabasePath *> $MigrationLog
if ($LASTEXITCODE -ne 0) {
  throw "旧数据库检查失败，请查看日志：$MigrationLog"
}
& $Python -m alembic -c backend\alembic.ini upgrade head *>> $MigrationLog
if ($LASTEXITCODE -ne 0) {
  throw "数据库升级失败，请查看日志：$MigrationLog"
}

$Mock = $null
if ($MockProvider) {
  $Mock = Start-Process -FilePath $Python `
    -ArgumentList @("scripts\mock-openai-responses.py", "--port", $MockProviderPort) `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $LogRoot "mock-provider.stdout.log") `
    -RedirectStandardError (Join-Path $LogRoot "mock-provider.stderr.log") `
    -PassThru
}

$Api = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", $ApiPort) `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogRoot "api.stdout.log") `
  -RedirectStandardError (Join-Path $LogRoot "api.stderr.log") `
  -PassThru

$Worker = Start-Process -FilePath $Python `
  -ArgumentList @("-m", "app.worker") `
  -WorkingDirectory $Root `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogRoot "worker.stdout.log") `
  -RedirectStandardError (Join-Path $LogRoot "worker.stderr.log") `
  -PassThru

$Frontend = Start-Process -FilePath $Npm `
  -ArgumentList @("run", "dev", "--", "--port", $FrontendPort, "--strictPort") `
  -WorkingDirectory (Join-Path $Root "frontend") `
  -WindowStyle Hidden `
  -RedirectStandardOutput (Join-Path $LogRoot "frontend.stdout.log") `
  -RedirectStandardError (Join-Path $LogRoot "frontend.stderr.log") `
  -PassThru

$State = [ordered]@{
  api_url = "http://127.0.0.1:$ApiPort"
  frontend_url = "http://127.0.0.1:$FrontendPort"
  log_directory = $LogRoot
  api_pid = $Api.Id
  worker_pid = $Worker.Id
  frontend_pid = $Frontend.Id
  mock_provider_url = if ($Mock) { "http://127.0.0.1:$MockProviderPort/v1" } else { $null }
  mock_provider_pid = if ($Mock) { $Mock.Id } else { $null }
}
$State | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $LogRoot "processes.json")

Write-Host "产品预览已启动：$($State.frontend_url)" -ForegroundColor Green
Write-Host "日志目录：$LogRoot"
