#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYINSTALLER_CONFIG_DIR="$ROOT_DIR/build/pyinstaller_cfg_sp_show_ctrl"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

VENV_DIR=".venv_build"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade --quiet pip
python -m pip install --upgrade --quiet pyinstaller certifi
python -m pip install --upgrade --quiet -r requirements.txt screeninfo

ICON_PNG="assets/logo.png"
ICON_ICNS="assets/app_icon.icns"
if [[ -f "$ICON_PNG" && ! -f "$ICON_ICNS" ]]; then
  echo "Generating macOS app icon: $ICON_ICNS"
  TMP_ICONSET="packaging/.tmp_appicon.iconset"
  TMP_SRC="packaging/.tmp_appicon_source.png"
  rm -rf "$TMP_ICONSET"
  mkdir -p "$TMP_ICONSET"
  python - <<PY
from pathlib import Path
from PIL import Image

src = Path("$ICON_PNG")
dst = Path("$TMP_SRC")
img = Image.open(src).convert("RGBA")
w, h = img.size
size = max(w, h)
canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
canvas.paste(img, ((size - w) // 2, (size - h) // 2), img)
canvas.save(dst)
PY
  for size in 16 32 64 128 256 512 1024; do
    sips -z "$size" "$size" "$TMP_SRC" --out "$TMP_ICONSET/icon_${size}x${size}.png" >/dev/null
  done
  cp "$TMP_ICONSET/icon_32x32.png" "$TMP_ICONSET/icon_16x16@2x.png"
  cp "$TMP_ICONSET/icon_64x64.png" "$TMP_ICONSET/icon_32x32@2x.png"
  cp "$TMP_ICONSET/icon_256x256.png" "$TMP_ICONSET/icon_128x128@2x.png"
  cp "$TMP_ICONSET/icon_512x512.png" "$TMP_ICONSET/icon_256x256@2x.png"
  cp "$TMP_ICONSET/icon_1024x1024.png" "$TMP_ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$TMP_ICONSET" -o "$ICON_ICNS"
  rm -rf "$TMP_ICONSET"
  rm -f "$TMP_SRC"
fi

ICON_ARGS=()
if [[ -f "$ICON_ICNS" ]]; then
  ICON_ARGS=(--icon "$ICON_ICNS")
fi

PYI_ARGS=(
  --noconfirm
  --windowed
  --name "SP Show Control"
  --collect-data certifi
  --collect-all tkinterdnd2
  --add-data "assets/logo.png:assets"
)

if [[ -f "$ICON_ICNS" ]]; then
  PYI_ARGS+=(--icon "$ICON_ICNS")
fi

if command -v mpv >/dev/null 2>&1; then
  MPV_PATH="$(command -v mpv)"
  PYI_ARGS+=(--add-binary "${MPV_PATH}:tools/mpv")
fi

pyinstaller "${PYI_ARGS[@]}" player.py

echo "Built: dist/SP Show Control.app"
