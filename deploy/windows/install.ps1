<#
  QMS Reminder System - first-time install / upgrade on a Windows server.
  Run in PowerShell from the backend folder:   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1
  Safe to run again after copying a new version (it only migrates and refreshes static files).
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root

if (-not (Test-Path ".venv")) {
    Write-Host "Creating Python virtual environment..."
    py -3.12 -m venv .venv
}
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
& $Py -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Write-Host "Creating .env from .env.example (edit it afterwards)..."
    $secret = & $Py -c "import secrets; print(secrets.token_urlsafe(50))"
    (Get-Content ".env.example") `
        -replace '^DJANGO_SECRET_KEY=.*', "DJANGO_SECRET_KEY=$secret" `
        -replace '^SQLITE_PATH=.*', "SQLITE_PATH=$Root\data\db.sqlite3" `
        -replace '^PRIVATE_UPLOAD_ROOT=.*', "PRIVATE_UPLOAD_ROOT=$Root\data\uploads" `
        -replace '^BACKUP_ROOT=.*', "BACKUP_ROOT=$Root\data\backups" `
        -replace '^LOG_DIR=.*', "LOG_DIR=$Root\logs" `
        -replace '^SQLITE_WAL=.*', "SQLITE_WAL=true" |
        Set-Content ".env" -Encoding UTF8
    Write-Warning "Edit $Root\.env now: DJANGO_ALLOWED_HOSTS, FRONTEND_URL, GRAPH_* values (see docs\DEPLOYMENT_WINDOWS.md)."
}

New-Item -ItemType Directory -Force -Path "data", "data\uploads", "data\backups", "logs" | Out-Null
& $Py manage.py migrate --noinput
& $Py manage.py createcachetable
& $Py manage.py collectstatic --noinput
& $Py manage.py check

if (-not (Test-Path "frontend_dist\index.html")) {
    Write-Warning "frontend_dist\index.html not found. Copy the built frontend (dist folder) into $Root\frontend_dist."
}
Write-Host ""
Write-Host "Done. Next: create the admin (see docs), then run deploy\windows\register-tasks.ps1 as Administrator."
