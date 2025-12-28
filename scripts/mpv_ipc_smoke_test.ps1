param(
  [string]$MpvPath,
  [int]$TimeoutSec,
  [switch]$Pause
)

$ErrorActionPreference = "Stop"

if ($null -eq $MpvPath) { $MpvPath = "" }
if ($null -eq $TimeoutSec -or $TimeoutSec -le 0) { $TimeoutSec = 8 }

function Get-LogDir {
  $base = ""
  if ($Env:APPDATA) { $base = $Env:APPDATA }
  if (-not $base) {
    $base = Join-Path $Env:USERPROFILE "AppData\Roaming"
  }
  return (Join-Path $base "SP_Show_Control\logs")
}

function Resolve-MpvPath {
  param([string]$Explicit)
  if ($Explicit) {
    if (Test-Path $Explicit) { return (Resolve-Path $Explicit).Path }
    throw "mpv not found at: $Explicit"
  }
  try { return (Get-Command mpv.exe -ErrorAction Stop).Source } catch {}
  try { return (Get-Command mpv -ErrorAction Stop).Source } catch {}

  $candidates = @()
  if ($Env:ProgramData) {
    $candidates += (Join-Path $Env:ProgramData "chocolatey\lib\mpv\tools\mpv.exe")
  }
  if ($Env:USERPROFILE) {
    $candidates += (Join-Path $Env:USERPROFILE "scoop\apps\mpv\current\mpv.exe")
  }
  $candidates += "C:\Program Files\mpv\mpv.exe"
  $candidates += "C:\Program Files (x86)\mpv\mpv.exe"
  foreach ($c in $candidates) {
    if (Test-Path $c) { return (Resolve-Path $c).Path }
  }
  return ""
}

function New-NamedPipeClient {
  param([string]$PipePath, [int]$TimeoutMs)
  $prefix = "\\.\pipe\"
  $name = $PipePath
  if ($PipePath.StartsWith($prefix)) {
    $name = $PipePath.Substring($prefix.Length)
  }
  if (-not $name) { throw "Invalid pipe name: $PipePath" }

  $dir = [System.IO.Pipes.PipeDirection]::InOut
  $opt = [System.IO.Pipes.PipeOptions]::Asynchronous
  $client = New-Object System.IO.Pipes.NamedPipeClientStream(".", $name, $dir, $opt)
  $client.Connect([Math]::Max(1, [int]$TimeoutMs))
  return $client
}

$mpv = Resolve-MpvPath -Explicit $MpvPath
if (-not $mpv) { throw "mpv.exe not found. Provide -MpvPath or install mpv." }

$guid = [Guid]::NewGuid().ToString("N")
$pipe = "\\.\pipe\sp_show_ctrl_ipc_test_$guid"
$logDir = Get-LogDir
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "mpv_ipc_smoke_test_$guid.log"

Write-Host "mpv: $mpv"
Write-Host "pipe: $pipe"
Write-Host "log : $logFile"
("mpv=$mpv`npipe=$pipe`nstarted=$(Get-Date -Format o)`n") | Out-File -FilePath $logFile -Encoding UTF8 -Append

$args = @(
  "--no-terminal",
  "--idle=yes",
  "--force-window=yes",
  "--keep-open=yes",
  "--msg-level=ipc=v",
  "--log-file=$logFile",
  "--input-ipc-server=$pipe"
)

$proc = $null
try {
  Write-Host "Starting mpv..."
  $proc = Start-Process -PassThru -FilePath $mpv -ArgumentList $args
  $timeoutMs = [Math]::Max(1000, $TimeoutSec * 1000)

  Write-Host "Connecting..."
  $fs = New-NamedPipeClient -PipePath $pipe -TimeoutMs $timeoutMs
  $enc = New-Object System.Text.UTF8Encoding($false)
  $reader = New-Object System.IO.StreamReader($fs, $enc, $true, 4096, $true)
  $writer = New-Object System.IO.StreamWriter($fs, $enc, 4096, $true)
  $writer.NewLine = "`n"
  $writer.AutoFlush = $true

  Write-Host "Sending request..."
  $req = @{ command = @("get_property", "mpv-version"); request_id = 1 } | ConvertTo-Json -Compress
  $writer.WriteLine($req)
  $task = $reader.ReadLineAsync()
  if (-not $task.Wait($timeoutMs)) { throw "Timeout waiting IPC response." }
  $line = $task.Result
  if (-not $line) { throw "No response from IPC." }
  $resp = $line | ConvertFrom-Json
  if ($resp.error -ne "success") { throw "IPC error: $($resp.error)" }
  Write-Host "OK: mpv-version=$($resp.data)"

  $quit = @{ command = @("quit"); request_id = 2 } | ConvertTo-Json -Compress
  $writer.WriteLine($quit)
  Write-Host "Quitting..."
} catch {
  $msg = ($_ | Out-String).Trim()
  Write-Host "ERROR:`n$msg"
  try { ("ERROR:`n$msg`n") | Out-File -FilePath $logFile -Encoding UTF8 -Append } catch {}
  throw
} finally {
  try { if ($proc -and -not $proc.HasExited) { $proc.Kill() | Out-Null } } catch {}
}

Write-Host "Done."
if ($Pause) {
  Write-Host "Press Enter to close..."
  [void](Read-Host)
}
