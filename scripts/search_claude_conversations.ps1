param(
    [Parameter(Mandatory = $true)]
    [string[]]$Pattern,

    [int]$Context = 2,

    [string]$ClaudeProjectDirectory = "C:\Users\ASUS\.claude\projects\D--Document-Downloads-AI-----"
)

$ErrorActionPreference = "Stop"

function Get-MessageText {
    param([object]$Record)

    if ($null -eq $Record.message) {
        return ""
    }

    $content = $Record.message.content
    if ($content -is [string]) {
        return $content
    }

    $parts = foreach ($item in @($content)) {
        if ($item.type -eq "text" -and $item.text) {
            $item.text
        }
    }
    return ($parts -join "`n")
}

$regex = ($Pattern | ForEach-Object { [regex]::Escape($_) }) -join "|"
$files = Get-ChildItem -LiteralPath $ClaudeProjectDirectory -Filter "*.jsonl" -File

foreach ($file in $files) {
    $records = [System.Collections.Generic.List[object]]::new()
    $lineNumber = 0

    foreach ($line in [System.IO.File]::ReadLines($file.FullName, [System.Text.Encoding]::UTF8)) {
        $lineNumber += 1
        try {
            $record = $line | ConvertFrom-Json
        }
        catch {
            continue
        }

        $text = Get-MessageText -Record $record
        if ([string]::IsNullOrWhiteSpace($text)) {
            continue
        }

        $records.Add([pscustomobject]@{
            Line = $lineNumber
            Role = $record.message.role
            Text = $text
        })
    }

    $hits = for ($index = 0; $index -lt $records.Count; $index += 1) {
        if ($records[$index].Text -match $regex) {
            $index
        }
    }

    if (-not $hits) {
        continue
    }

    $selected = [System.Collections.Generic.SortedSet[int]]::new()
    foreach ($hit in $hits) {
        $start = [Math]::Max(0, $hit - $Context)
        $end = [Math]::Min($records.Count - 1, $hit + $Context)
        for ($index = $start; $index -le $end; $index += 1) {
            [void]$selected.Add($index)
        }
    }

    "===== $($file.Name) ====="
    foreach ($index in $selected) {
        $record = $records[$index]
        "--- line $($record.Line) role=$($record.Role) ---"
        $record.Text
    }
}
