<#
  Registers three Windows scheduled tasks (run PowerShell as Administrator):
    QMS Reminders - Web        : starts the web server at system start-up and restarts it if it stops
    QMS Reminders - Scheduler  : every 15 minutes, sends the daily mails when due (once per day)
    QMS Reminders - Backup     : daily database backup at 07:30 (before the 08:00 mails)
  The tasks run as the service account given below, which must be able to read this folder.
  Use -ServiceUser SYSTEM (no password) only if IT prefers the built-in system account.
#>
param(
    [Parameter(Mandatory = $true)][string]$ServiceUser,
    [string]$ServicePassword
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
$Ps = "powershell.exe"
if ($ServiceUser -in @("SYSTEM", "NT AUTHORITY\SYSTEM")) {
    $Common = @{ User = "NT AUTHORITY\SYSTEM"; RunLevel = "Highest"; Force = $true }
} else {
    if (-not $ServicePassword) { throw "Give -ServicePassword for $ServiceUser (or use -ServiceUser SYSTEM)." }
    $Common = @{ User = $ServiceUser; Password = $ServicePassword; RunLevel = "Limited"; Force = $true }
}

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "QMS Reminders - Web" -Settings $settings @Common `
    -Action (New-ScheduledTaskAction -Execute $Ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Root\deploy\windows\run-server.ps1`"" -WorkingDirectory $Root) `
    -Trigger (New-ScheduledTaskTrigger -AtStartup)

$every15 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 15)
$short = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "QMS Reminders - Scheduler" -Settings $short @Common `
    -Action (New-ScheduledTaskAction -Execute $Ps -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Root\deploy\windows\run-scheduler.ps1`"" -WorkingDirectory $Root) `
    -Trigger $every15

Register-ScheduledTask -TaskName "QMS Reminders - Backup" -Settings $short @Common `
    -Action (New-ScheduledTaskAction -Execute "$Root\.venv\Scripts\python.exe" -Argument "manage.py backup_db --keep 30" -WorkingDirectory $Root) `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "07:30")

Start-ScheduledTask -TaskName "QMS Reminders - Web"
Write-Host "Scheduled tasks registered. Open http://localhost:8000/api/health/ to check the web server."
