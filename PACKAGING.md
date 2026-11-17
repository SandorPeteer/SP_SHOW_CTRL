## Packaging (macOS + Windows)

Ez a repó két appot buildel:

- **S.P. Show Control** (`player.py`)
- **yt-dlr (DJ)** (`ytdlr_app.py` / `./yt-dlr --qt`)

### Build előfeltételek

- Windows x64 buildhez 64 bites Python 3.11+ szükséges (Tcl/Tk-val).
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

### Windows x64 hordozható kiadás

A teljes `SP-Show-Control-windows.zip` fájlt csomagold ki egy mappába, és onnan indítsd az **SP Show Control.exe** fájlt. Az `_internal` mappa az EXE mellett maradjon. A lejátszáshoz, hullámformához és médiainformációkhoz a csomag tartalmazza az mpv-t, az FFmpeg-et, az ffprobe-ot és az ffplay-t. Python csak a fejlesztői gépre kell. Windows alatt a PPT/PPTX hozzáadásakor vagy a műsor megnyitásakor az app a háttérben készíti elő a diákat a helyi asztali PowerPointtal. A PPT lapon az **Előkészítés** gombbal is indítható. Várd meg a **Kész** jelzést. Az mpv-ablakot húzd a kivetítőre, kapcsold be a **PRESENTATION** módot, majd a PPT lapon használd a **Start**, **◀**, **▶** és **End** gombot. A képek, videók és diák ugyanabban az ablakban jelennek meg. A B deck a diákat alapból némítja, a videódia hangját a **Dia saját hangja** kapcsoló engedi át.

A statikus PPTX-diák képként, a felismert animált vagy médiás diák és a régi PPT-formátum diái videóként készülnek el. A videódia a saját időzítése szerint fut, a végén a kép megáll. A diák között kézzel léptetsz. A dián belüli kattintásokat, hivatkozásokat, összetett áttűnéseket és az egész bemutatón átívelő hangot próbáld végig előre. Külső médiafájlt vigyél magaddal, és frissített külső média esetén ments új PPTX-et beágyazott médiával.

A B decken a **Kivetítő** előnézet a futó mpv-kimenetből fél másodpercenként kér képet. A **Következő dia** panel az aktív bemutató következő diáját mutatja. Indítás előtt a kiválasztott kép, videó vagy előkészített PPT első diája látszik benne. A régebben előkészített videódiákhoz az app a videó első képkockáját használja, az új előkészítések külön statikus diaképet is tartalmaznak. A mintavételezett kimeneti kép a lejátszó képét követi, a fizikai projektor és HDMI-kapcsolat állapotáról nem ad visszajelzést.

A hullámforma a csomagban lévő FFmpeg-et és Pillow-t használja. A háttérben egyszerre egy elemzési feladat fut, új kijelöléskor a korábbi hullámformakérés megszakad. A várakozást és a hibát a hullámforma helyén mutatja az app.

A build ellenőrzi a letöltött médiacsomagok SHA-256 értékét. A Windows-próba a frissen elkészült EXE-t külön profilból nyitja meg, és egy generált videóval ellenőrzi az mpv-kimenetet:
```powershell
.\.venv_build_windows\Scripts\python.exe scripts\windows_bundle_smoke.py
```

### Runtime toolok / függőségek

**S.P. Show Control**
- FFmpeg (`ffmpeg`, `ffprobe`, és `ffplay` ha ffplay backendet használsz).
- Ha hiányzik, az app fel tudja ajánlani az FFmpeg letöltését (BtbN build).
- Tool mappa override: `SP_SHOW_CTRL_FFMPEG_DIR` vagy `SP_SHOW_CTRL_TOOLS_DIR`.

**mpv**
- Ajánlott a stabil 2nd screen outputhoz, de az app nem tölti le automatikusan.
- Buildkor: ha `mpv` telepítve van a build gépen, a `build_macos.sh` be tudja csomagolni.

**yt-dlr**
- `yt-dlp` szükséges (ajánlott: rendszerre telepítve), de a Qt GUI tud standalone `yt-dlp`-t letölteni: `Tools → Update yt-dlp`.
- `ffmpeg` csak akkor kell, ha CLI módban A/V merge-et csinálsz.

### CI (GitHub Actions)

- Workflow: `.github/workflows/build.yml`
- Verziótag pushra és kézi futtatásra is buildel macOS + Windows artifactokat.

