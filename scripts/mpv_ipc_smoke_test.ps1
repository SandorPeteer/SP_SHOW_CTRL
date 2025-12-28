param(
  [string]$MpvPath,
  [int]$TimeoutSec
)

$ErrorActionPreference = "Stop"

if ($null -eq $MpvPath) { $MpvPath = "" }
if ($null -eq $TimeoutSec -or $TimeoutSec -le 0) { $TimeoutSec = 8 }

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

function Wait-NamedPipe {
  param([string]$PipePath, [int]$TimeoutMs)
  Add-Type -Namespace Win32 -Name Pipe -MemberDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Pipe {
  [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
  public static extern bool WaitNamedPipe(string name, int timeout);
}
"@ -ErrorAction SilentlyContinue | Out-Null

  $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMs)
  while ([DateTime]::UtcNow -lt $deadline) {
    try {
      if ([Win32.Pipe]::WaitNamedPipe($PipePath, 200)) { return $true }
    } catch {}
    Start-Sleep -Milliseconds 60
  }
  return $false
}

$mpv = Resolve-MpvPath -Explicit $MpvPath
if (-not $mpv) { throw "mpv.exe not found. Provide -MpvPath or install mpv." }

$guid = [Guid]::NewGuid().ToString("N")
$pipe = "\\.\pipe\sp_show_ctrl_ipc_test_$guid"
$logDir = Join-Path $Env:APPDATA "SP_Show_Control\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir "mpv_ipc_smoke_test_$guid.log"

Write-Host "mpv: $mpv"
Write-Host "pipe: $pipe"
Write-Host "log : $logFile"

$args = @(
  "--no-terminal",
  "--idle=yes",
  "--force-window=yes",
  "--keep-open=yes",
  "--msg-level=ipc=v",
  "--log-file=$logFile",
  "--input-ipc-server=$pipe"
)

$proc = Start-Process -PassThru -FilePath $mpv -ArgumentList $args
try {
  $timeoutMs = [Math]::Max(1000, $TimeoutSec * 1000)
  if (-not (Wait-NamedPipe -PipePath $pipe -TimeoutMs $timeoutMs)) {
    throw "Timeout waiting for named pipe."
  }

  $fs = New-Object System.IO.FileStream($pipe, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
  $enc = New-Object System.Text.UTF8Encoding($false)
  $reader = New-Object System.IO.StreamReader($fs, $enc, $true, 4096, $true)
  $writer = New-Object System.IO.StreamWriter($fs, $enc, 4096, $true)
  $writer.NewLine = "`n"
  $writer.AutoFlush = $true

  $req = @{ command = @("get_property", "mpv-version"); request_id = 1 } | ConvertTo-Json -Compress
  $writer.WriteLine($req)
  $line = $reader.ReadLine()
  if (-not $line) { throw "No response from IPC." }
  $resp = $line | ConvertFrom-Json
  if ($resp.error -ne "success") { throw "IPC error: $($resp.error)" }
  Write-Host "OK: mpv-version=$($resp.data)"

  $quit = @{ command = @("quit"); request_id = 2 } | ConvertTo-Json -Compress
  $writer.WriteLine($quit)
} finally {
  try { if ($proc -and -not $proc.HasExited) { $proc.Kill() | Out-Null } } catch {}
}

Write-Host "Done."
