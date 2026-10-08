param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("export", "score")]
    [string]$Command,

    [string]$RunId,
    [string]$Gold,
    [string]$Output,
    [int]$SampleStart = -1,
    [int]$SampleChars = 50000,
    [string]$ApiBase = "http://127.0.0.1:18000",
    [switch]$AllowDraft
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Missing .venv. Run scripts\setup.ps1 first."
}

$env:PYTHONPATH = Join-Path $ProjectRoot "backend"

if ($Command -eq "export") {
    if (-not $RunId) {
        throw "RunId is required for export."
    }
    if (-not $Output) {
        $Output = Join-Path $ProjectRoot "workspace\evaluation\$RunId\gold.json"
    }
    $Arguments = @(
        "-m", "app.evaluation", "export",
        "--run-id", $RunId,
        "--api-base", $ApiBase,
        "--output", $Output,
        "--sample-chars", $SampleChars
    )
    if ($SampleStart -ge 0) {
        $Arguments += @("--sample-start", $SampleStart)
    }
}
else {
    if (-not $Gold) {
        throw "Gold is required for score."
    }
    if (-not $Output) {
        $Output = Join-Path (Split-Path -Parent (Resolve-Path $Gold)) "report"
    }
    $Arguments = @(
        "-m", "app.evaluation", "score",
        "--gold", $Gold,
        "--api-base", $ApiBase,
        "--output-dir", $Output
    )
    if ($AllowDraft) {
        $Arguments += "--allow-draft"
    }
}

& $Python @Arguments
if ($LASTEXITCODE -ne 0) {
    throw "Gold benchmark failed with exit code $LASTEXITCODE."
}
