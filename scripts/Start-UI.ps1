param([int]$Port = 18764, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $launchArgs = @('-X','utf8','-B','-m','partner_monitor.launcher','--port',"$Port")
    if ($NoBrowser) { $launchArgs += '--no-browser' }
    & python @launchArgs
    if ($LASTEXITCODE -ne 0) { throw 'Launcher failed. Check Python dependencies or choose another -Port.' }
} finally { Pop-Location }
