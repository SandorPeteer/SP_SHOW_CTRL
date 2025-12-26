## Packaging (macOS + Windows)

Ez a repó két appot buildel:

- **S.P. Show Control** (`player.py`)
- **yt-dlr (DJ)** (`ytdlr_app.py` / `./yt-dlr --qt`)

### Build előfeltételek

- Python 3.10+ (ajánlott)
- Internet (a build scriptek pip-pel telepítik a szükséges csomagokat a helyi `.venv_build`-be)

### Build parancsok

macOS:
```bash
./packaging/build_macos.sh
./packaging/build_yt_dlr_macos.sh
```

Windows (PowerShell):
```powershell
.\packaging\build_windows.ps1
.\packaging\build_yt_dlr_windows.ps1
```

Kimenet:
- macOS: `dist/*.app`
- Windows: `dist/*/*.exe`

### Runtime toolok / függőségek

**S.P. Show Control**
- FFmpeg (`ffmpeg`, `ffprobe`, és `ffplay` ha ffplay backendet használsz).
- Ha hiányzik, az app fel tudja ajánlani az FFmpeg letöltését (BtbN build).
- Tool mappa override: `SP_SHOW_CTRL_FFMPEG_DIR` vagy `SP_SHOW_CTRL_TOOLS_DIR`.

**mpv**
- Ajánlott a stabil 2nd screen outputhoz, de az app nem tölti le automatikusan.
- Buildkor: ha `mpv` telepítve van a build gépen, a `build_macos.sh` / `build_windows.ps1` be tudja csomagolni.

**yt-dlr**
- `yt-dlp` szükséges (ajánlott: rendszerre telepítve), de a Qt GUI tud standalone `yt-dlp`-t letölteni: `Tools → Update yt-dlp`.
- `ffmpeg` csak akkor kell, ha CLI módban A/V merge-et csinálsz.

### CI (GitHub Actions)

- Workflow: `.github/workflows/build.yml`
- Pushra és kézi futtatásra is buildel macOS + Windows artifactokat.

