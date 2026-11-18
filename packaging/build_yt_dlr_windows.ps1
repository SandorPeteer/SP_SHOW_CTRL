$ErrorActionPreference = "Stop"
function Run([string]$Exe, [string[]]$Arguments) {
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)" }
}

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Venv = ".venv_build_windows_ytdlr"
if (!(Test-Path $Venv)) {
  Run python @("-m", "venv", $Venv)
}

$Py = Join-Path $Venv "Scripts\python.exe"
$Pi = Join-Path $Venv "Scripts\pyinstaller.exe"

Run $Py @("-c", "import sys,platform; assert sys.version_info >= (3,11) and platform.machine().upper() in ('AMD64','X86_64'), 'Python x64 3.11+ required'")
Run $Py @("-m", "pip", "install", "--upgrade", "--quiet", "pyinstaller>=6,<7", "PyQt6", "certifi")

$Args = @(
  "--noconfirm",
  "--clean",
  "--noconsole",
  "--name", "yt-dlr",
  "--collect-data", "PyQt6",
  "--collect-binaries", "PyQt6",
  "--collect-data", "certifi",
  "--collect-submodules", "PyQt6.QtMultimedia",
  "--collect-submodules", "PyQt6.QtMultimediaWidgets",
  "--add-data", "assets\logo.png;assets"
)

if (Test-Path "assets\app_icon.ico") {
  $Args += @("--icon", "assets\app_icon.ico")
}

Run $Pi ($Args + @("ytdlr_app.py"))
if (!(Test-Path "dist/yt-dlr/yt-dlr.exe")) { throw "Missing built executable" }

Write-Host "Built: dist\yt-dlr\yt-dlr.exe"
