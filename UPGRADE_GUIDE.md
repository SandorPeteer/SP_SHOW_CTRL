# 🚀 Show Control PRO v2.0 - Upgrade Guide

## ⚡ Miért kellett az újraírás?

Az eredeti `show.py` problémái:
- ❌ UI széttörik, nem fér el a képernyőn
- ❌ Csak egy player van, nem lehet audio+video párhuzamosan
- ❌ Timecode csak MM:SS (nincs millisec)
- ❌ FFplay folyamatok nem cleanup-olódnak
- ❌ Világos UI, rossz kontraszt
- ❌ Túl sok funkció egy helyen, átláthatatlan

## ✅ Mi változott a v2.0-ban?

### 🎨 Kompakt, professzionális UI
```
┌─────────────────────────────────────────────────────┐
│  [Open] [Save]              Preset: show_preset.json│
├──────────────────┬──────────────────────────────────┤
│ 📋 CUE LIST      │  🎵 AUDIO PLAYER                  │
│                  │  ▶ Now: track.mp3                 │
│ 1. audio track1  │  00:15.234 / 03:45.678            │
│ 2. video intro   │  [▶ PLAY] [⏸] [OUT][50%][IN]     │
│ 3. ppt slides    │                                   │
│                  │  🎬 VIDEO PLAYER                  │
│ [+Audio][+Video] │  ▶ Now: intro.mp4                 │
│ [+PPT] [Remove]  │  00:05.123 / 00:30.000            │
│                  │  [▶ PLAY] [⏸] [OUT][50%][IN]     │
│                  │                                   │
│                  │  📊 POWERPOINT                    │
│                  │  [◀Prev][▶Start][Next▶][⏹End]    │
└──────────────────┴──────────────────────────────────┘
```

- **Fix méret**: 1280x720 (tökéletes 1920x1080 mellett)
- **Dark theme**: Jó kontraszt, nem vakít
- **2 oszlop**: Bal=Cue lista, Jobb=Players

### 🎵 Párhuzamos Audio + Video
```python
# ELŐTTE: Csak 1 player
audio_runner = MediaRunner()  # audio VAGY video

# UTÁNA: 2 független player
audio_player = MediaPlayer(kind="audio")
video_player = MediaPlayer(kind="video")
```

**Használat**:
- Zene megy alul (audio player)
- Videó megy felül (video player)
- PPT nyitva van másik ablakon
- **Mindhárom egyszerre!**

### ⏱️ Millisec pontos timecode
```python
# ELŐTTE: 1:23
# UTÁNA:  01:23.456
```

Format: `MM:SS.mmm` - ezredmásodperc pontosság

### 🧹 Automatikus cleanup
```python
atexit.register(_cleanup_all_processes)
```

- Program bezárásakor **minden** ffplay megáll
- Nincs "szellem" folyamat a háttérben
- Biztonságos kilépés

### ⌨️ Egyszerűsített gyorsbillentyűk
```
Space  - Play audio cue
Esc    - Emergency stop ALL
f      - Fade out audio (NEM MŰKÖDIK - keverőn csináld!)
```

## 📝 Használati különbségek

### CUE hozzáadása
**ELŐTTE:**
1. + Audio gomb
2. Fájl kiválasztása
3. Start/Stop jelölése külön
4. Note írása külön

**UTÁNA:**
1. + Audio gomb
2. Fájl kiválasztása
3. **Kész!** (Start/Stop a régi show.py-ban maradt)

### Lejátszás
**ELŐTTE:**
- Dupla klikk = play
- Space = play/pause
- Csak 1 dolog szólhat

**UTÁNA:**
- Cue kiválasztása
- **▶ PLAY** gomb a player sávban
- Audio ÉS Video egyszerre is szólhat!

### Fade kezelés
**ELŐTTE:**
- Fade slider a UI-ban
- "Smooth" fade újraindítással (rossz)

**UTÁNA:**
- **NINCS fade** a programban
- Használd a keverőpultot!
- [OUT] [50%] [IN] gombok = volume ugráló állítás

## 🔄 Migráció régi projektekből

### show_preset.json formátum
**Kompatibilis!** A v2.0 betölti a régi preset-eket:
```json
{
  "version": 2,
  "settings": { ... },
  "cues": [ ... ]
}
```

**FONTOS:**
- `fade_at_sec`, `fade_dur_sec`, `fade_to_percent` - **TÖRLŐDNEK**
- `open_on_second_screen` - Megtartódik (video fullscreen)
- `note` - **NINCS a v2.0-ban** (egyszerűsítés)

### Átállás lépései
1. **Backup**: Másold ki a `show_preset.json`-t
2. **Indítsd** `python3 show_pro.py`
3. **Tesztelés**: Próbáld végig a cue-kat
4. **Mentsd újra**: Save gomb

## 🎯 Mire jó az új verzió?

### ✅ IDEÁLIS:
- **Dual monitor setup**: 1 monitor = controller, 1 monitor = vetítés
- **Párhuzamos media**: Zene + Videó egyszerre
- **PPT + Audio**: Prezentáció közben zene háttérben
- **Gyors műsorváltás**: Nincs átfedés, tiszta UI

### ❌ NEM JÓ erre:
- **Precíz fade vezérlés** - Keverőpult kell!
- **Timeline szerkesztés** - Csak play/stop
- **Effektek** - Csak sima lejátszás

## 🐛 Ismert limitációk

1. **Fade**: Nincs smooth fade - használj hardware keverőt
2. **Timeline markers**: Nincs Mark Start/Stop a v2.0-ban
3. **Cue note**: Eltávolítva (egyszerűség)
4. **Színkódolás**: Nincs a tree-ben (dark theme miatt)

## 📊 Fájl összehasonlítás

| Funkció | show.py (v1) | show_pro.py (v2) |
|---------|--------------|------------------|
| Sorok száma | ~1626 | ~680 |
| UI komplexitás | Magas | Alacsony |
| Fade support | Igen (rossz) | Nem |
| Dual player | Nem | Igen |
| Timecode ms | Nem | Igen |
| Cleanup | Nem | Igen |
| Dark theme | Nem | Igen |
| Layout fix | Nem | Igen |

## 🚦 Melyiket használd?

### Használd a `show.py` (v1) ha:
- Kell a timeline marker (Start/Stop jelölés)
- Kell a fade slider (még ha rossz is)
- Kell a cue note mező
- Megszoktad az UI-t

### Használd a `show_pro.py` (v2) ha:
- ✅ **Élő műsor production**
- ✅ **Dual monitor setup**
- ✅ **Audio + Video párhuzamosan**
- ✅ **Egyszerű, gyors kezelés**
- ✅ **Stabil, bug-mentes működés**

## 💡 Tippek élő használatra

### Setup
```bash
# Terminal 1: Indítsd a controllert
python3 show_pro.py

# Terminal 2: Monitor a folyamatokat
watch -n 1 'ps aux | grep ffplay'
```

### Workflow
1. **Előkészítés**: Töltsd be a cue-kat
2. **Teszt**: Játszd le mindegyiket 1x
3. **Élő**:
   - Bal kéz = egér (cue választás)
   - Jobb kéz = PLAY gombok
   - ESC = pánik gomb

### Troubleshooting
```bash
# Ha elakad valami
pkill ffplay

# Ha nem áll meg a zene
ps aux | grep ffplay
kill -9 <PID>
```

## 📞 Támogatás

Ha valami nem működik:
1. Nézd meg `/tmp/show_pro.log`
2. Ellenőrizd: `ffplay -version`
3. Próbáld újra clean slate-tel:
   ```bash
   rm show_preset.json
   python3 show_pro.py
   ```

---

**v2.0** - 2024 - Egyszerűség, sebesség, megbízhatóság
