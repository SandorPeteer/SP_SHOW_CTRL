# 🎬 Show Control PRO

**Professzionális élő műsorvezérlő alkalmazás iskolák számára**

## ✨ Új Funkciók (PRO verzió)

### 🎚️ Valós idejű Fade In/Out vezérlés
- **Live fade gombok**: Azonnali fade out (0%) és fade in (100%)
- **Fade slider**: Folyamatos hangerő szabályozás 0-100% között
- **FFmpeg audio filter alapú**: Sima, professzionális átmenetek
- Működik audio ÉS videó fájloknál is!

### ⌨️ Gyorsbillentyűk élő műsorra
- **Space** - Play/Pause (szünet/folytatás)
- **Esc** - Vészleállítás (emergency stop)
- **N vagy →** - Következő cue / GO LIVE
- **F** - Gyors fade out (0%)
- **U** - Gyors fade in/up (100%)
- **[** és **]** - 5 másodperces seek hátra/előre
- **M** - Start pozíció jelölése
- **.** - Stop pozíció jelölése

### 🎨 Modern UI/Design
- **Színkódolt cue lista**:
  - 🔵 Audio fájlok - világoskék háttér
  - 🟢 Videók - világoszöld háttér
  - 🟠 PowerPoint - világos narancssárga háttér
- **Ikonok mindenhol**: Könnyebb navigáció
- **Nagyobb, jobb láthatóságú gombok**
- **Professzionális címkézés**

### 🔴 GO LIVE! funkció
- Egy gombnyomással indítod a következő cue-t
- Automatikus továbblépés a cue lista végén
- Vizuális visszajelzés: mi szól éppen

## 📋 Alapvető használat

### Cue lista kezelése
1. **+ Audio / + Video / + PPT** gombokkal adj hozzá elemeket
2. **Up/Down** gombokkal sorrendezd őket
3. Dupla kattintással játszd le a kiválasztott elemet

### Timeline markerek
1. Játszd le a fájlt (dupla katt vagy Space)
2. A megfelelő időpillanatban nyomd meg:
   - **M** vagy "⏵ Mark Start" - kezdőpont jelölése
   - **.** vagy "⏹ Mark Stop" - végpont jelölése
3. A cue csak a kijelölt szakaszt fogja lejátszani

### Élő műsor indítása
1. Válaszd ki az első cue-t
2. Nyomd meg a **🔴 GO LIVE!** gombot (vagy N billentyűt)
3. A lejátszás végén automatikusan ugrás a következőre
4. Bármikor fade-elhetsz az **F** (out) vagy **U** (in) billentyűkkel
5. Vagy használd a fade slidert finomhangoláshoz

### Második képernyőre vetítés
- Videóknál a "Target" beállításnál válaszd a **"2nd screen"**-t
- Állítsd be a Settings-ben a második képernyő pozícióját (bal, felső koordináták)
- Full screen opció is elérhető

## 🛠️ Technikai követelmények

- **Python 3.7+**
- **FFmpeg** telepítve (ffplay és ffprobe)
  - macOS: `brew install ffmpeg`
  - Windows: [ffmpeg.org](https://ffmpeg.org/download.html)
  - Linux: `sudo apt install ffmpeg`
- **Microsoft PowerPoint** (opcionális, csak PPT cue-khoz, macOS-en AppleScript vezérlés)

## 🚀 Indítás

```bash
python3 show.py
```

Az alkalmazás automatikusan betölti a `show_preset.json` fájlt, ha létezik.

## 💾 Preset vs Show fájlok

- **Preset** (`show_preset.json`): Automatikusan betöltődik indításkor, gyors hozzáférés
- **Show fájlok** (`.json`): Különböző műsorok mentése/betöltése

### Save preset
A jelenlegi cue lista mentése preset-ként (auto-load következő indításkor)

### Save / Save As
Show fájl mentése tetszőleges helyre

## 🎯 Tippek élő használatra

1. **Előkészület**: Jelöld meg előre az összes start/stop pontot
2. **Teszt futtatás**: Próbáld végig a teljes műsort
3. **Gyorsbillentyűk**: Tanítsd be magadnak őket, sokkal gyorsabb!
4. **Fade gyakorlás**: Próbáld ki a fade funkciókat előre
5. **Vészleállítás**: Esc mindig stop-ol mindent

## 🎬 Munkafolyamat példa

```
1. Nyisd meg az alkalmazást
2. + Audio - zenei kíséret hozzáadása
3. + Video - bevezető videó
4. + PPT - diasor
5. Jelöld be a zenénél, hol kezdődjön (M)
6. Jelöld be, hol érjen véget (.)
7. Save preset - hogy legközelebb is meglegyen
8. GO LIVE! - indítás
9. F billentyű - fade out a zene végén
10. Következő cue automatikusan indul
```

## 📝 Changelog (PRO verzió)

- ✅ Valós idejű fade in/out FFmpeg filterekkel
- ✅ Gyorsbillentyűk teljes támogatása
- ✅ Színkódolt cue lista (audio/video/ppt)
- ✅ Modern, ikonos felhasználói felület
- ✅ Live fade slider folyamatos vezérléshez
- ✅ Professzionális címkék és gombok
- ✅ Nagyobb, jobb láthatóságú UI elemek

## 🐛 Problémamegoldás

**Nem indul az alkalmazás:**
- Ellenőrizd, hogy Python 3.7+ telepítve van
- `python3 --version`

**Nem játszik le semmit:**
- Ellenőrizd az ffmpeg telepítését: `ffplay -version`
- Nézd meg a log-ot az alkalmazás alján

**Fade nem működik:**
- Csak audio/video fájloknál működik
- Ellenőrizd, hogy épp szól-e valami
- Nézd meg a debug log-ot

## 📧 Támogatás

Ha hibát találsz, vagy kérdésed van, nézd meg a debug log-ot az alkalmazás alján.

---

**Készítette**: Show Control PRO Team
**Verzió**: 2.0 PRO
**Platform**: macOS / Windows / Linux
