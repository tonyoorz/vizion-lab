param(
  [string]$PythonLauncher = "py",
  [string]$PythonVersion = "-3.11",
  [string]$LogPath = "",
  [int]$CookieRefreshHeadless = 0
)

# Keeps the Octane SSO cookie fresh on a fixed cadence (every 4 hours by default,
# registered via register-octane-cookie-refresh-task.ps1). Runs the lightweight
# refresh-octane-cookie analytics command and writes a timestamped log alongside the
# nightly refresh logs. Unlike run-nightly-source-refresh.ps1 this script does NOT
# retry on auth failure — its whole purpose is to proactively refresh the cookie so
# the data refresh jobs do not hit 401 in the first place. A failure here is logged
# and surfaced via the exit code; the next scheduled run will try again.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$latestLogPath = $null
$logEncoding = New-Object System.Text.UTF8Encoding($false)
$logWriteWarningShown = $false

if ([string]::IsNullOrWhiteSpace($LogPath)) {
  $logDirectory = Join-Path $repoRoot "database\hot\logs"
  if (-not (Test-Path $logDirectory)) {
    New-Item -ItemType Directory -Path $logDirectory | Out-Null
  }
  $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $LogPath = Join-Path $logDirectory ("cookie-refresh-" + $timestamp + ".log")
  $latestLogPath = Join-Path $logDirectory "cookie-refresh.log"
}

function Write-LogLine {
  param(
    [string]$Message,
    [switch]$NoConsole
  )

  $lineText = $Message + [Environment]::NewLine

  foreach ($targetPath in @($LogPath, $latestLogPath)) {
    if ([string]::IsNullOrWhiteSpace($targetPath)) {
      continue
    }

    try {
      [System.IO.File]::AppendAllText($targetPath, $lineText, $logEncoding)
    }
    catch {
      if (-not $logWriteWarningShown) {
        $logWriteWarningShown = $true
        Write-Warning ("Failed to append cookie refresh log to " + $targetPath + ": " + $_.Exception.Message)
      }
    }
  }

  if (-not $NoConsole) {
    Write-Host $Message
  }
}

$startedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine -Message "[$startedAt] Starting octane-cookie-refresh"
Write-LogLine -Message ("[$startedAt] Log file: " + $LogPath)
Write-LogLine -Message ("[$startedAt] Runtime config cookie_refresh_headless=" + [bool]$CookieRefreshHeadless)
$effectiveHeadless = [bool]$CookieRefreshHeadless

function Format-CmdArgument {
  param([string]$Value)

  if ($null -eq $Value -or $Value.Length -eq 0) {
    return '""'
  }

  if ($Value -notmatch '[\s"&|<>^()]') {
    return $Value
  }

  return '"' + ($Value -replace '"', '""') + '"'
}

function Invoke-AnalyticsCli {
  param(
    [string[]]$CliArguments,
    [string]$CommandId
  )

  $previousPythonUtf8 = $env:PYTHONUTF8
  $previousPythonIoEncoding = $env:PYTHONIOENCODING
  $previousErrorActionPreference = $ErrorActionPreference
  $outputLines = New-Object 'System.Collections.Generic.List[string]'

  $env:PYTHONUTF8 = "1"
  $env:PYTHONIOENCODING = "utf-8"
  $ErrorActionPreference = "Continue"
  try {
    $started = Get-Date
    Write-LogLine -Message ("[" + ($started.ToString("yyyy-MM-dd HH:mm:ss")) + "] Running " + $CommandId + ": " + ($PythonLauncher + " " + (($CliArguments | ForEach-Object { Format-CmdArgument $_ }) -join " ")))
    try {
      & $PythonLauncher @CliArguments 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) {
          $line = $_.Exception.Message
        }
        else {
          $line = $_.ToString()
        }

        if (-not [string]::IsNullOrWhiteSpace($line)) {
          $outputLines.Add($line)
          Write-LogLine -Message $line
        }
      }

      $commandExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
    }
    catch {
      $commandExitCode = 1
      $line = $_.Exception.Message
      if ([string]::IsNullOrWhiteSpace($line)) {
        $line = $_.ToString()
      }

      if (-not [string]::IsNullOrWhiteSpace($line)) {
        $outputLines.Add($line)
        Write-LogLine -Message $line
      }
    }

    $finished = Get-Date
    $durationSeconds = [Math]::Round((New-TimeSpan -Start $started -End $finished).TotalSeconds, 1)
    Write-LogLine -Message ("[" + ($finished.ToString("yyyy-MM-dd HH:mm:ss")) + "] Completed " + $CommandId + " exit_code=" + $commandExitCode + " duration_seconds=" + $durationSeconds)
  }
  finally {
    $ErrorActionPreference = $previousErrorActionPreference

    if ($null -eq $previousPythonUtf8) {
      Remove-Item Env:PYTHONUTF8 -ErrorAction SilentlyContinue
    }
    else {
      $env:PYTHONUTF8 = $previousPythonUtf8
    }

    if ($null -eq $previousPythonIoEncoding) {
      Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    }
    else {
      $env:PYTHONIOENCODING = $previousPythonIoEncoding
    }
  }

  return @{
    ExitCode = $commandExitCode
    OutputLines = $outputLines.ToArray()
  }
}

$cookieArguments = @(
  $PythonVersion,
  "-m",
  "backend.analytics_cli",
  "refresh-octane-cookie"
)

if ($effectiveHeadless) {
  $cookieArguments += "--headless"
}

$cookieResult = Invoke-AnalyticsCli -CliArguments $cookieArguments -CommandId "cookie-refresh"

$exitCode = $cookieResult.ExitCode
$finishedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-LogLine -Message "[$finishedAt] Finished octane-cookie-refresh exit_code=$exitCode"

exit $exitCode
