#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYINSTALLER_CONFIG_DIR="$ROOT_DIR/build/pyinstaller_cfg"
mkdir -p "$PYINSTALLER_CONFIG_DIR"

VENV_DIR=".venv_build"
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
python -m pip install --upgrade --quiet pip
python -m pip install --upgrade --quiet pyinstaller PyQt6 certifi

ICON_ARGS=()
if [[ -f "assets/app_icon.icns" ]]; then
  ICON_ARGS=(--icon "assets/app_icon.icns")
fi

pyinstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "yt-dlr" \
  "${ICON_ARGS[@]}" \
  --collect-data certifi \
  --add-data "assets/logo.png:assets" \
  --collect-submodules PyQt6.QtMultimedia \
  --collect-submodules PyQt6.QtMultimediaWidgets \
  --workpath "build/pyinstaller_yt_dlr" \
  --distpath "dist" \
  ytdlr_app.py

echo "Built: dist/yt-dlr.app"
