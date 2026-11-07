<#
  Post-deployment smoke test for the QMS Reminder System on Windows (test cases INS-01..INS-12).
  Run on the server from the backend folder after install / upgrade:
      powershell -ExecutionPolicy Bypass -File deploy\windows\smoke-test.ps1 [-BaseUrl https://qms-reminders.company.local] [-Online]
  Exit code 0 = all checks passed (warnings allowed), 1 = at least one failure.
  Read-only: sends no mail and changes no data.
#>
param(
    [string]$BaseUrl = "http://localhost:8000",
    [switch]$Online
)
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$failures = 0
function Report($status, $name, $detail) {
    $color = @{ PASS = "Green"; WARN = "Yellow"; FAIL = "Red" }[$status]
    Write-Host ("{0}  {1,-34} {2}" -f $status, $name, $detail) -ForegroundColor $color
    if ($status -eq "FAIL") { $script:failures++ }
}

Write-Host "== Application checks (manage.py deploy_check) =="
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Report FAIL "Python environment" "$py missing - run install.ps1" }
else {
    $checkArgs = @("manage.py", "deploy_check"); if ($Online) { $checkArgs += "--online" }
    & $py @checkArgs
    if ($LASTEXITCODE -ne 0) { $failures++ }
}

Write-Host ""
Write-Host "== Web server =="
try {
    $health = Invoke-RestMethod -Uri "$BaseUrl/api/health/" -TimeoutSec 15
    if ($health.status -eq "ok") { Report PASS "Health endpoint" "$BaseUrl/api/health/" } else { Report FAIL "Health endpoint" ($health | ConvertTo-Json -Compress) }
} catch { Report FAIL "Health endpoint" $_.Exception.Message }
try {
    $page = Invoke-WebRequest -Uri "$BaseUrl/" -UseBasicParsing -TimeoutSec 15
    if ($page.StatusCode -eq 200 -and $page.Content -match 'id="root"') { Report PASS "App page served" "$BaseUrl/" } else { Report FAIL "App page served" "HTTP $($page.StatusCode)" }
} catch { Report FAIL "App page served" $_.Exception.Message }
try {
    $resp = Invoke-WebRequest -Uri "$BaseUrl/api/simple/plants/" -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
    Report FAIL "API requires sign-in" "unauthenticated request was answered with HTTP $($resp.StatusCode)"
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    if ($code -eq 401) { Report PASS "API requires sign-in" "HTTP 401 without a session" } else { Report WARN "API requires sign-in" "unexpected: $($_.Exception.Message)" }
}

Write-Host ""
Write-Host "== Scheduled tasks =="
foreach ($name in "QMS Reminders - Web", "QMS Reminders - Scheduler", "QMS Reminders - Backup") {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { Report FAIL $name "not registered - run register-tasks.ps1"; continue }
    $info = Get-ScheduledTaskInfo -TaskName $name
    if ($task.State -eq "Disabled") { Report FAIL $name "disabled" }
    elseif ($info.LastRunTime -lt (Get-Date "2000-01-01")) { Report WARN $name "registered, not run yet; next run $($info.NextRunTime)" }
    elseif ($info.LastTaskResult -ne 0 -and $task.State -ne "Running") { Report FAIL $name ("last result 0x{0:X} at {1}" -f $info.LastTaskResult, $info.LastRunTime) }
    else { Report PASS $name "state $($task.State); last run $($info.LastRunTime); next $($info.NextRunTime)" }
}

Write-Host ""
Write-Host "== Files =="
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    $acl = (Get-Acl $envFile).Access | Where-Object { $_.IdentityReference -match "Everyone|BUILTIN\\Users|Authenticated Users" }
    if ($acl) { Report WARN ".env permissions" "readable by $($acl.IdentityReference -join ', ') - restrict to Administrators + service account" } else { Report PASS ".env permissions" "" }
    if (Select-String -Path $envFile -Pattern '^ADMIN_PASSWORD=.+' -Quiet) { Report FAIL ".env ADMIN_PASSWORD" "remove the bootstrap password from .env" }
}
$backups = Get-ChildItem (Join-Path $Root "data\backups") -Filter "qa-reminders-*.sqlite3*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
if ($backups) {
    $age = (Get-Date) - $backups[0].LastWriteTime
    if ($age.TotalHours -lt 30) { Report PASS "Latest backup" "$($backups[0].Name)" } else { Report WARN "Latest backup" "$([int]$age.TotalHours) h old" }
} else { Report WARN "Latest backup" "none yet (the Backup task runs daily at 07:30)" }
foreach ($log in "web.log", "scheduler.log") {
    $p = Join-Path $Root "logs\$log"
    if (Test-Path $p) {
        $errors = Select-String -Path $p -Pattern '"level": "ERROR"|Traceback' -ErrorAction SilentlyContinue | Select-Object -Last 3
        if ($errors) { Report WARN "logs\$log" "recent errors: $($errors[-1].Line.Substring(0, [Math]::Min(120, $errors[-1].Line.Length)))" } else { Report PASS "logs\$log" "no errors" }
    }
}

Write-Host ""
if ($failures -gt 0) { Write-Host "SMOKE TEST FAILED ($failures failure(s))." -ForegroundColor Red; exit 1 }
Write-Host "SMOKE TEST PASSED." -ForegroundColor Green
exit 0
