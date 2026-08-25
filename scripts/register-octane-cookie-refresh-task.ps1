param(
  [string]$TaskName = "VizionLab Octane Cookie Refresh",
  [int]$IntervalHours = 4,
  [string]$StartTime = "00:00",
  [string]$RunScriptPath = (Join-Path $PSScriptRoot "run-octane-cookie-refresh.ps1"),
  [string]$RunAsUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
  [int]$PrintCommandOnly = 0,
  [switch]$InteractiveOnly
)

# Registers a scheduled task that proactively refreshes the Octane SSO cookie on a
# fixed cadence (default every 4 hours, aligned to StartTime). The cookie expires
# after roughly 4 hours, so keeping it fresh prevents the data refresh jobs from
# hitting 401 and paying the cost of a reactive cookie refresh + retry on every run.
#
# By default the task runs in interactive mode (matches the existing nightly source
# refresh tasks) because Octane SSO + Windows Hello PIN login requires an interactive
# desktop session. Use -InteractiveOnly:$false only if the account can run the
# Playwright login unattended (typically it cannot).

$ErrorActionPreference = "Stop"

$resolvedRunScriptPath = (Resolve-Path $RunScriptPath).Path
$taskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $resolvedRunScriptPath

if ([int]$PrintCommandOnly -ne 0) {
  Write-Host $taskCommand
  exit 0
}

$schtasksArguments = @(
  "/Create",
  "/SC",
  "HOURLY",
  "/MO",
  $IntervalHours.ToString(),
  "/ST",
  $StartTime,
  "/TN",
  $TaskName,
  "/TR",
  $taskCommand,
  "/F"
)

if ($InteractiveOnly) {
  $schtasksArguments += "/IT"
}
else {
  Write-Host "Task Scheduler will prompt for the Windows password of '$RunAsUser'."
  Write-Host "Type it directly in the terminal so the task can run whether you are logged on or not."
  $schtasksArguments += @(
    "/RU",
    $RunAsUser,
    "/RP",
    "*"
  )
}

$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
  $output = & schtasks.exe @schtasksArguments 2>&1
  $exitCode = $LASTEXITCODE
}
finally {
  $ErrorActionPreference = $previousErrorActionPreference
}

if ($exitCode -ne 0) {
  throw ($output | Out-String)
}

$output | Out-String | Write-Host
if ($InteractiveOnly) {
  Write-Host "Registered scheduled task '$TaskName' in interactive-only mode to run every $IntervalHours hour(s) starting at $StartTime."
}
else {
  Write-Host "Registered scheduled task '$TaskName' to run every $IntervalHours hour(s) starting at $StartTime whether you are logged on or not."
}
