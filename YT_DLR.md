# yt-dlr (DJ)

YouTube keresés → beágyazott preview → rekordbox-kompatibilis audio letöltés (m4a/AAC prefer) `yt-dlp`-vel.

## Ajánlott indítás (Qt GUI)

```bash
./yt-dlr --qt
```

Alternatíva:

```bash
python3 ytdlr_entry.py --qt
```

## Qt DJ GUI – mit tud

- Keresés a felső listában (cím/csatorna/hossz/nézettség).
- Katt a találatra: formátumok + auto-preview indul (alapból audio).
- Preview: beágyazott (Qt Multimedia), seek/progress csúszkával.
- `DJ Download (m4a)`: letöltés a beállított `Watch folder`-be, progress bar + státusz sor.
- Formátumlista: rekordbox-barát audio opciók (m4a/AAC), alapból a “best” kerül kiválasztásra.

### Keresés opciók (felső sáv)

- `Limit`: hány találatot kérjen le (max 200).
- `Newest`: a legújabb feltöltések között keres (yt-dlp `ytsearchdate`).
- `DJ filter`: próbálja kiszűrni a többórás seteket/long mixeket és előresorolni a DJ-barát címeket (remix/edit/extended/club/mashup).
- `Max`: maximum hossz (ha a keresésben nincs kifejezetten “mix/set/live”).
- `Prefer official`: “official audio / Topic / VEVO” találatokat feljebb sorol.

### yt-dlp választás + frissítés

- `Tools → Use system yt-dlp (recommended)`
- `Tools → Use managed yt-dlp (app data)`
- `Tools → Update yt-dlp` (standalone bináris letöltése a user app-data alá)
- `Tools → Show active yt-dlp`

SSL hiba updaternél (`CERTIFICATE_VERIFY_FAILED`) esetén megpróbálja a system `curl` fallbackot; ha mégis gond, telepíts CA bundle-t:

```bash
python3 -m pip install certifi
```

### Logok

- `./yt-dlr --qt` alapból némítja a Qt Multimedia zajt és log fájlba terel.
- Debug mód: `./yt-dlr --qt --debug`
- macOS log fájl: `~/Library/Logs/yt-dlr/yt-dlr.log`

## CLI mód (közvetlen letöltés)

Legjobb elérhető A/V (ha kell merge, `ffmpeg` szükséges):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" -P ~/Downloads
```

Egyfájlos (nincs merge):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" --single-file -P ~/Downloads
```

Csak hang (eredeti formátumban, nincs konvert):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" --mode audio -P ~/Downloads
```

Playlist letöltés tiltása:

```bash
./yt-dlr "https://www.youtube.com/playlist?list=..." --no-playlist -P ~/Downloads
```

További `yt-dlp` kapcsolók átadása (pl. cookie):

```bash
./yt-dlr "https://www.youtube.com/watch?v=VIDEO_ID" -P ~/Downloads --cookies-from-browser chrome
```

## Legacy Tk GUI (régi)

Ha valamiért nem működik a Qt GUI, elérhető a régi Tkinter-es GUI is (`mpv` / `ffplay` alapú preview-val):

```bash
./yt-dlr --gui
```

