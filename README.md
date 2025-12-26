# SP_SHOW_CTRL

Ebben a repóban **két külön app** van:

- **S.P. Show Control** (`player.py`): élő műsor / vetítés vezérlő (A/B deck, Scene-ek, 2nd screen output, audio/video/PPT cue-k).
- **yt-dlr (DJ)** (`./yt-dlr --qt`): YouTube keresés → beágyazott preview → rekordbox-barát audio letöltés (`yt-dlp`).

Kapcsolódó doksik:
- `PACKAGING.md` (macOS + Windows build)
- `YT_DLR.md` (yt-dlr használat)

## S.P. Show Control

### Fő funkciók (GUI)

- **Scene-ek**: Scene lista, `PREV/NEXT`, opcionális auto-advance.
- **Deck A / Deck B**: külön cue-listák audio/video/PPT elemekkel.
- **IN/OUT pontok**: cue-onként vágási pontok (időkód mezők + waveform marker).
- **Waveform**: generálás + kattintható markerek (audio/video).
- **Kivetítés / 2nd screen output**: mpv (ajánlott) vagy ffplay backend, 2nd screen pozíció beállítással.
- **PowerPoint cue**: PPT megnyitás + slideshow vezérlés (platformfüggő; macOS-en Accessibility engedély kellhet).

### Indítás (forrásból)

```bash
python3 player.py
```

### Preset

- Forrásból futtatva a preset fájl: `./show_preset.json`
- Buildelt appban a preset a user app-data mappába kerül (pl. macOS: `~/Library/Application Support/SP_Show_Control/show_preset.json`)

## yt-dlr (DJ)

Indítás:

```bash
./yt-dlr --qt
```

Részletek: `YT_DLR.md`

## Build (macOS + Windows)

Részletek: `PACKAGING.md`

Gyors parancsok:

```bash
./packaging/build_macos.sh
./packaging/build_yt_dlr_macos.sh
```

Windows (PowerShell):

```powershell
.\packaging\build_windows.ps1
.\packaging\build_yt_dlr_windows.ps1
```

