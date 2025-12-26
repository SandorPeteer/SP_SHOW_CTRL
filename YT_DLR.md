# yt-dlr

Egyszerű parancssori wrapper a `yt-dlp` köré, YouTube tartalmak letöltéséhez **újrakódolás/konvertálás nélkül**.

## Előfeltételek

- `yt-dlp`
- `ffmpeg` (csak a legjobb A/V letöltéshez, amikor a videó és a hang külön stream)

macOS (Homebrew):

```bash
brew install yt-dlp ffmpeg
```

## Használat

## GUI (keresés + letöltés)

## Qt DJ GUI (ajánlott)

```bash
./yt-dlr --qt
```

- Keresés (felső lista), dupla katt a találatra → formátumlista (m4a/AAC, rekordbox kompatibilis) + preview.
- `DJ Download (m4a)` a beállított `Watch folder`-be ment.
- Preview: beágyazott (Qt Multimedia), seek/progress csúszkával; alapból a legjobb elérhető audio (m4a/AAC, ha van).
- Letöltés: progress bar + státusz sor a GUI-ban.
- yt-dlp frissítés: `Tools → Update yt-dlp` (standalone binárist tölt le a user app-data alá, pl. macOS: `~/Library/Application Support/yt-dlr/tools/`).
- Aktív yt-dlp választás: `Tools → Use system yt-dlp` vagy `Tools → Use managed yt-dlp (app data)`, ellenőrzés: `Tools → Show active yt-dlp`.
- Ha a terminált teleírja a preview backend, indítsd így: `./yt-dlr --qt` (alapból log fájlba megy); debughoz: `./yt-dlr --qt --debug`.
- Ha az updater SSL hibával leáll (`CERTIFICATE_VERIFY_FAILED`): most már megpróbálja a system `curl`-t fallbackként; ha mégis gond van, telepíts CA bundle-t: `python3 -m pip install certifi`.

Keresés opciók (felső sáv):

- `Limit`: hány találatot kérjen le (max 200).
- `Newest`: a “legújabb feltöltések” között keres (yt-dlp `ytsearchdate`).
- `DJ filter`: igyekszik kiszűrni a hosszú seteket / többórás mixeket és előresorolja a DJ-barát találatokat (remix/edit/extended, official audio, Topic/VEVO).
- `Max`: maximum hossz (ha nincs kifejezetten “mix/set/live” a keresésben).
- `Prefer official`: “official audio / Topic / VEVO” találatok feljebb kerülnek.

Indítás:

```bash
./yt-dlr --gui
```

Alternatíva (ha nem futtatható a wrapper):

```bash
python3 ytdlr_entry.py --gui
```

Direkt a GUI fájl:

```bash
python3 ytdlr_gui.py
```

Itt elég beírnod egy keresőkifejezést, kiválasztod a találatot, és `Download`.

### Preview

- `Preview` gomb: a kiválasztott videót lejátsza külső playerben (ajánlott: `mpv`).
- Ha nincs `mpv`, megpróbálja `ffplay`-jel (ehhez a GUI `yt-dlp -g`-vel kér egy direct stream URL-t egyfájlos preview-hoz).
- `Embed preview (mpv)`: ha be van kapcsolva, a preview megpróbál a GUI-n belül megjelenni (mpv `--wid`).

### DJ Rush (Rekordbox)

- Állítsd be a `Watch folder`-t a Rekordbox automatikus import mappájára.
- `DJ Download (m4a)`: audio-only letöltés, m4a/AAC preferálva (`-f` bestaudio m4a), hogy Rekordbox-barát legyen.

### Rendezés

- A találatok és a formátumlista oszlopfejléceire kattintva rendezhetsz (fel/le).

### Recordbox / mp4

- A GUI-ban van `List formats` gomb: kiválasztott videónál kilistázza az elérhető formátumokat (alapból csak `mp4/m4a`).
- Dupla katt a formátumlistán: betölti a `-f` mezőbe a kiválasztott `format_id`-t.
- Ha videó+hangot külön streamben ad a YouTube, jelöld ki 1 videó (`Type=v`) és 1 hang (`Type=a`) sort (több kijelölés), majd `Use selected` → automatikusan `VID+AID` lesz.
- `Prefer Recordbox (mp4/h264+aac)` bekapcsolva: a letöltés megpróbál mp4/H.264/AAC verziót választani, és csak ha nincs, akkor eshet vissza másra.

Legjobb elérhető A/V (bestvideo+bestaudio, szükség esetén csak mux/merge):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" -P ~/Downloads
```

Ha *szigorúan* egyetlen, eredetileg is egy fájlként elérhető formátumot szeretnél (nincs merge):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" --single-file -P ~/Downloads
```

Csak hang (eredeti formátumban, nincs audio-konvertálás):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" --mode audio -P ~/Downloads
```

Playlist letöltés tiltása:

```bash
./yt-dlr "https://www.youtube.com/playlist?list=..." --no-playlist -P ~/Downloads
```

További `yt-dlp` kapcsolók átadása (pl. `--cookies-from-browser`), a `yt-dlr` kapcsolói után:

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" -P ~/Downloads --cookies-from-browser chrome
```
