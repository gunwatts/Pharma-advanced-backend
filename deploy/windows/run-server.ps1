<#
  Starts the web application (API + frontend) with Waitress on the configured port.
  Normally started by the "QMS Reminders - Web" scheduled task at system start-up.
#>
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$Port = if ($env:QMS_PORT) { $env:QMS_PORT } else { "8000" }
& "$Root\.venv\Scripts\waitress-serve.exe" --listen="0.0.0.0:$Port" --threads=8 --trusted-proxy=127.0.0.1 `
    --trusted-proxy-headers="x-forwarded-proto x-forwarded-for" config.wsgi:application *>> "$Root\logs\web.log"
