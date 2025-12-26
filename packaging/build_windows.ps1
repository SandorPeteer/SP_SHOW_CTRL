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
& $Py -m pip install --upgrade --quiet pyinstaller pillow certifi
& $Py -m pip install --upgrade --quiet -r requirements.txt screeninfo

$MpvExe = ""
try {
  $MpvExe = (Get-Command mpv -ErrorAction Stop).Source
} catch {
  $MpvExe = ""
}

$IconPng = "assets\logo.png"
$IconIco = "assets\app_icon.ico"
if ((Test-Path $IconPng) -and !(Test-Path $IconIco)) {
  @'
from pathlib import Path
try:
    from PIL import Image
except Exception as e:
    raise SystemExit(f"Pillow required to generate icon: {e}")

png = Path("assets/logo.png")
ico = Path("assets/app_icon.ico")
img0 = Image.open(png).convert("RGBA")
w, h = img0.size
size = max(w, h)
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
img.paste(img0, ((size - w) // 2, (size - h) // 2), img0)
sizes = [(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)]
img.save(ico, format="ICO", sizes=sizes)
print("Generated", ico)
'@ | & $Py -
}

$Args = @(
  "--noconfirm",
  "--clean",
  "--noconsole",
  "--name", "SP Show Control",
  "--collect-data", "certifi",
  "--collect-all", "tkinterdnd2",
  "--add-data", "assets\\logo.png;assets"
)

if (Test-Path $IconIco) {
  $Args += @("--icon", "$IconIco")
}

if ($MpvExe) {
  $Args += @("--add-binary", "$MpvExe;tools\\mpv")
}

& $Pi @Args "player.py"

Write-Host "Built: dist\\SP Show Control\\SP Show Control.exe"
