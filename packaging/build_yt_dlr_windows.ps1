$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Venv = ".venv_build"
if (!(Test-Path $Venv)) {
  python -m venv $Venv
}

$Py = Join-Path $Venv "Scripts\python.exe"
$Pi = Join-Path $Venv "Scripts\pyinstaller.exe"

& $Py -m pip install --upgrade --quiet pip
& $Py -m pip install --upgrade --quiet pyinstaller PyQt6 certifi

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

& $Pi @Args "ytdlr_app.py"

Write-Host "Built: dist\yt-dlr\yt-dlr.exe"
