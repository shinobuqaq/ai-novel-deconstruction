param(
    [string]$ExampleDirectory = (Join-Path $PSScriptRoot "filled_example")
)

$ErrorActionPreference = "Stop"

$expectedFiles = @(
    "00_开书总表.md",
    "01_人物与关系.md",
    "02_世界与规则.md",
    "03_剧情与开篇.md",
    "04_连载规则与账本.md"
)

$errors = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()
$stats = [System.Collections.Generic.List[object]]::new()
$texts = @{}

foreach ($name in $expectedFiles) {
    $path = Join-Path $ExampleDirectory $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        $errors.Add("缺少正式实例文件：$name")
        continue
    }

    $text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    $texts[$name] = $text
    $lineCount = (Get-Content -LiteralPath $path -Encoding UTF8).Count
    $stats.Add([pscustomobject]@{
        File = $name
        Characters = $text.Length
        Lines = $lineCount
        Headings = ([regex]::Matches($text, "(?m)^#{1,6}\s")).Count
    })

    if ($text -match "(?im)\bTODO\b|\bTBD\b|待补|稍后补充") {
        $errors.Add("$name 含未处理占位词")
    }
}

$actualFiles = Get-ChildItem -LiteralPath $ExampleDirectory -Filter "*.md" -File |
    Select-Object -ExpandProperty Name
$unexpected = @($actualFiles | Where-Object { $_ -notin $expectedFiles })
if ($unexpected.Count -gt 0) {
    $warnings.Add("实例目录出现五份正式文档之外的 Markdown 文件：$($unexpected -join '、')")
}

$crossChecks = @(
    @{ Files = @("00_开书总表.md", "02_世界与规则.md"); Terms = @("十分钟", "七天", "三笔") },
    @{ Files = @("01_人物与关系.md", "03_剧情与开篇.md"); Terms = @("林砚", "许照", "周烬") },
    @{ Files = @("00_开书总表.md", "03_剧情与开篇.md"); Terms = @("父亲", "第一卷") },
    @{ Files = @("02_世界与规则.md", "04_连载规则与账本.md"); Terms = @("债务", "纸质记录", "清账会") },
    @{ Files = @("01_人物与关系.md", "04_连载规则与账本.md"); Terms = @("林砚", "许照", "杜野", "周烬") }
)

foreach ($check in $crossChecks) {
    foreach ($file in $check.Files) {
        if (-not $texts.ContainsKey($file)) {
            continue
        }
        foreach ($term in $check.Terms) {
            if ($texts[$file] -notmatch [regex]::Escape($term)) {
                $errors.Add("$file 缺少跨文件一致性关键词：$term")
            }
        }
    }
}

$contentLines = foreach ($name in $expectedFiles) {
    if (-not $texts.ContainsKey($name)) {
        continue
    }
    foreach ($line in ($texts[$name] -split "\r?\n")) {
        $trimmed = $line.Trim()
        if (
            $trimmed.Length -ge 24 -and
            $trimmed -notmatch "^#|^\||^>|^-{3,}$|^\d+\.\s"
        ) {
            [pscustomobject]@{ File = $name; Line = $trimmed }
        }
    }
}

$duplicates = $contentLines |
    Group-Object -Property Line |
    Where-Object { $_.Count -gt 1 } |
    ForEach-Object {
        [pscustomobject]@{
            Count = $_.Count
            Text = $_.Name
            Files = ($_.Group.File | Sort-Object -Unique) -join "、"
        }
    }

[pscustomobject]@{
    Passed = ($errors.Count -eq 0)
    Directory = $ExampleDirectory
    FileCount = $actualFiles.Count
    TotalCharacters = ($stats | Measure-Object -Property Characters -Sum).Sum
    Stats = $stats
    ExactDuplicateParagraphs = @($duplicates)
    Warnings = $warnings
    Errors = $errors
} | ConvertTo-Json -Depth 6
