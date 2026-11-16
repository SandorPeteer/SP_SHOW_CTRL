$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed (exit $LASTEXITCODE)" }
}

$Py = ".venv_build_windows\Scripts\python.exe"
if (!(Test-Path $Py)) { Run python @("-m", "venv", ".venv_build_windows") }
Run $Py @("-c", "import sys,platform; assert sys.version_info >= (3,11) and platform.machine().upper() in ('AMD64','X86_64'), 'Python x64 3.11+ required'")
Run $Py @("-m", "pip", "install", "pyinstaller>=6,<7", "certifi", "-r", "requirements.txt")
Run $Py @("packaging/windows_media_tools.py", "build/windows-tools", "build/windows-media")
New-Item -ItemType Directory -Force build/windows-spec | Out-Null
Run $Py @("-m", "PyInstaller", "--clean", "--noconfirm", "--windowed", "--noupx",
    "--name", "SP Show Control", "--icon", "$(Resolve-Path assets/logo.png)", "--specpath", "build/windows-spec",
    "--collect-data", "certifi", "--collect-all", "tkinterdnd2",
    "--add-data", "$(Resolve-Path assets/logo.png);assets",
    "--add-data", "$(Resolve-Path build/windows-media);tools", "$(Resolve-Path player.py)")
$App = "dist/SP Show Control"
if (!(Test-Path "$App/SP Show Control.exe")) { throw "Missing built executable" }
Write-Host "Built: $App/SP Show Control.exe"
