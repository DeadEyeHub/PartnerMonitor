param(
    [string]$Run,
    [string]$RuntimeRoot = "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies",
    [switch]$SkipReport,
    [switch]$Preview
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Push-Location $projectRoot
try {
    $node = Join-Path $RuntimeRoot 'node/bin/node.exe'
    $packages = Join-Path $RuntimeRoot 'node/node_modules'
    if (!(Test-Path -LiteralPath $node) -or !(Test-Path -LiteralPath "$packages/@oai/artifact-tool")) {
        throw 'Bundled spreadsheet runtime unavailable. Set -RuntimeRoot to your workspace dependency runtime.'
    }
    if (!$SkipReport) {
        $reportArgs = @('compose','run','--rm','collector','report')
        if ($Run) { $reportArgs += @('--run',$Run) }
        & docker @reportArgs
        if ($LASTEXITCODE -ne 0) { throw 'Report generation failed' }
    }
    $junction = Join-Path $PSScriptRoot 'node_modules'
    if (!(Test-Path -LiteralPath $junction)) {
        New-Item -ItemType Junction -Path $junction -Target $packages | Out-Null
    }
    $buildArgs = @((Join-Path $PSScriptRoot 'build-workbook.mjs'),'data/reports/latest.workbook.json','data/reports/latest.xlsx')
    if ($Preview) { $buildArgs += 'data/reports/workbook-preview' }
    & $node @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Workbook generation failed' }
    # Add the link only after the matching workbook has been successfully exported.
    $reportPath = Join-Path $projectRoot 'data/reports/latest.html'
    $html = [IO.File]::ReadAllText($reportPath)
    if (!$html.Contains('href="latest.xlsx"')) {
        $html = $html.Replace('<h2>Overview</h2>', '<p><a href="latest.xlsx">Download Excel workbook</a></p><h2>Overview</h2>')
        [IO.File]::WriteAllText($reportPath,$html,[Text.UTF8Encoding]::new($false))
    }
} finally { Pop-Location }
