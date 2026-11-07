<#
  Runs due jobs once (daily reminder mails at the configured time, at most once per day) and exits.
  Called every 15 minutes by the "QMS Reminders - Scheduler" scheduled task.
#>
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
& "$Root\.venv\Scripts\python.exe" manage.py run_scheduler *>> "$Root\logs\scheduler.log"
