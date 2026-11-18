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

A Show Control Windows build a `.venv_build_windows` környezetet használja; a külön yt-dlr app Qt6-függőségei a `.venv_build_windows_ytdlr` környezetbe kerülnek. Forrásból a Windows-verzió ugyanezzel a Show Control környezettel indítható a build után:
```powershell
.\.venv_build_windows\Scripts\python.exe player.py
```
Ez a környezet tartalmazza a `requirements.txt` mellett a buildben rögzített PySide6-Essentials és numpy függőséget is.

Kimenet:
- macOS: `dist/*.app`
- Windows: `dist/*/*.exe`

### Windows x64 hordozható kiadás

A teljes `SP-Show-Control-windows.zip` fájlt csomagold ki egy mappába, és onnan indítsd az **SP Show Control.exe** fájlt. Az `_internal` mappa az EXE mellett maradjon. A lejátszáshoz, hullámformához és médiainformációkhoz a csomag tartalmazza az mpv-t, az FFmpeg-et, az ffprobe-ot és az ffplay-t. Python csak a fejlesztői gépre kell. Windows alatt a PPT/PPTX hozzáadásakor vagy a műsor megnyitásakor az app a háttérben készíti elő a diákat a helyi asztali PowerPointtal. A PPT lapon az **Előkészítés** gombbal is indítható. Várd meg a **Kész** jelzést. Az mpv-ablakot húzd a kivetítőre, kapcsold be a **PRESENTATION** módot, majd a PPT lapon használd a **Start**, **◀**, **▶** és **End** gombot. A képek, videók és diák ugyanabban az ablakban jelennek meg. A B deck a diákat alapból némítja, a videódia hangját a **Dia saját hangja** kapcsoló engedi át.

A statikus PPTX-diák képként, a felismert animált vagy médiás diák és a régi PPT-formátum diái videóként készülnek el. A videódia a saját időzítése szerint fut, a végén a kép megáll. A B deck **LOOP ON** gombja a teljes PPT diasorozatát ismétli. A képek a PPT-ben megadott időzítést követik. Ennek hiányában a PPT lap **Időzítés nélkül (s)** mezője adja meg a várakozást, alapból 5 másodpercet. A videódiák végigfutnak, majd következik a következő dia. **LOOP OFF** mellett kézzel léptetsz. A dián belüli kattintásokat, hivatkozásokat, összetett áttűnéseket és az egész bemutatón átívelő hangot próbáld végig előre. Külső médiafájlt vigyél magaddal, és frissített külső média esetén ments új PPTX-et beágyazott médiával.

A Windows-felület egységes sötét témát használ. A kijelölt fül kék, a feliratok minden állapotban kontrasztosak. A PPT lapon az **Export mappa** gombbal megnyitható az elkészült képek és videók mappája, így ugyanaz az MP4 más lejátszóban is ellenőrizhető.

A B decken a **Kivetítő** előnézet a futó mpv-kimenetből fél másodpercenként kér képet. A **Következő dia** panel az aktív bemutató következő diáját mutatja. Indítás előtt a kiválasztott kép, videó vagy előkészített PPT első diája látszik benne. A régebben előkészített videódiákhoz az app a videó első képkockáját használja, az új előkészítések külön statikus diaképet is tartalmaznak. A mintavételezett kimeneti kép a lejátszó képét követi, a fizikai projektor és HDMI-kapcsolat állapotáról nem ad visszajelzést.

A Windows-csomag a yt-dlp-t és a YouTube JavaScript-feladataihoz szükséges Denót is tartalmazza. A **Download → Letöltő frissítése** gomb mindkettő aktuális hivatalos kiadását letölti az alkalmazás saját felhasználói mappájába. A letöltés SHA-256 ellenőrzés és sikeres próbaindítás után váltja a korábbi programot. A státuszban megjelennek a verziók. Hiányzó letöltőeszközt az első Download automatikusan pótol. Frissítéshez és online letöltéshez internetkapcsolat kell. A PPT-k első előkészítéséhez a gépre telepített asztali PowerPoint szükséges.

A hullámforma a csomagban lévő FFmpeg-et és Pillow-t használja. A háttérben egyszerre egy elemzési feladat fut, új kijelöléskor a korábbi hullámformakérés megszakad. A várakozást és a hibát a hullámforma helyén mutatja az app.

A build ellenőrzi a letöltött médiacsomagok SHA-256 értékét. A Windows-próba a frissen elkészült EXE-t külön profilból nyitja meg, és egy generált videóval ellenőrzi az mpv-kimenetet:
```powershell
.\.venv_build_windows\Scripts\python.exe scripts\windows_bundle_smoke.py
```

### Windows kiadói aláírás és Smart App Control

A build alapból **aláíratlan fejlesztői csomag**. A `dist/windows-signatures.json` minden csomagolt EXE/DLL/PYD/COM fájl x64 fejlécét, SHA-256 értékét, Authenticode állapotát és kiadóját rögzíti; a CI ezt a képernyőképekkel együtt feltölti. Ez ellenőrzési jelentés, nem SAC-kompatibilitási igazolás.

A már rendelkezésre álló, megbízható szolgáltatótól származó kiadói tanúsítvány explicit módon beköthető:
```powershell
.\packaging\build_windows.ps1 -CertificateThumbprint "<CurrentUser\My tanúsítvány SHA-1 azonosítója>" -TimestampUrl "<a szolgáltató RFC 3161 címe>"
```
Az aláírókulcs a Windows tanúsítványtárban marad; a szkript nem exportálja, nem telepít tanúsítványt, nem választ szolgáltatót, és nem módosít Windows-védelmet. A `-SignToolPath` a már telepített Windows SDK `signtool.exe` fájlját adhatja meg. Csak az aláíratlan fájlokat írja alá; a meglévő hibás aláírást nem írja felül. SHA-256 és időbélyeg után minden binárison `signtool verify /pa /all` fut, és hiba esetén megáll a build. Külső aláírási szolgáltatás eredményét külön is ellenőrizheted:
```powershell
.\packaging\windows_signing.ps1 -AppDirectory "dist/SP Show Control" -RequireSigned
```

Az [Microsoft SAC útmutatója](https://learn.microsoft.com/en-us/windows/apps/develop/smart-app-control/code-signing-for-smart-app-control) megbízható szolgáltatótól származó tanúsítványt követel; önaláírás nem oldja meg ezt. A [teljes alkalmazáskódra vonatkozó útmutató](https://learn.microsoft.com/en-us/windows/security/book/application-security-application-and-driver-control) miatt az mpv, FFmpeg, Python/Qt DLL-ek és letöltőeszközök aláírása is számít, nem csak a fő EXE-é. A `yt-dlp`/Deno későbbi frissítése új futtatható fájlt hoz: a SHA-256 és próbaindítás nem helyettesít kiadói aláírást. A SAC melletti működés mindaddig **nincs megoldva**, amíg nincs hiteles aláírás és a teljes aktuális csomaggal, valamint a frissítővel végzett SAC-próba. A repóhoz továbbra sem választottunk alkalmazáslicencet vagy tanúsítványszolgáltatót.

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

