# Live Operation Update - Changelog

## Áttekintés
Az SP Show Control teljes mértékben átalakítva profi élő műsorvezetésre. Minden funkció optimalizálva gyorsaságra és megbízhatóságra.

## ✅ Elkészült funkciók

### 1. Preview ablak (Elsődleges képernyő)
- **Deck A és Deck B** mindkettő rendelkezik 120px magas preview ablakkal
- **Automatikus frissítés** amikor cue-t választunk
- **Videó/kép**: ffmpeg-gel automatikusan kivon egy frame-et thumbnail-ként
- **Audio**: ikon + fájlnév megjelenítése
- **PPT**: prezentáció ikon + fájlnév
- **Aszinkron működés** - nem blokkolja a UI-t

**Fájlok**: `player.py:1565-1687`, `player.py:3057`, `player.py:3085`

### 2. Fekete képernyő (Másodlagos kimenet)
- **BlackScreenWindow** osztály - singleton pattern
- **Automatikus megjelenés** az app indulásakor a second screen-en
- **Fekete fullscreen** ablak "● OUTPUT READY ●" felirattal
- **Automatikus bezárás** amikor bármilyen média indul (play/play_at)
- **Tiszta lezárás** az app bezárásakor

**Fájlok**: `player.py:1383-1433`, `player.py:3070-3077`, `player.py:841`, `player.py:894`

### 3. iPad Extended Display detektálás
- **"🔍 Detect Screens"** gomb a Display beállításokban
- **macOS Quartz API** használat pontos képernyő pozíció detektáláshoz
- **Automatikus iPad felismerés** - működik negatív koordinátákkal is (pl. -1080, 0)
- **Fallback mód** ha Quartz nem elérhető
- **Platformfüggetlen** - működik macOS/Windows/Linux-on

**Fájlok**: `player.py:4815-4872`, `player.py:2092`

### 4. Jelölőnégyzet alapú törlés
- **Checkbox oszlop** width=0 alapértelmezetten (láthatatlan)
- **Remove gomb első kattintás**: megjeleníti a checkboxokat
- **Második kattintás**: törli a kiválasztott elemeket
- **Gyors és pontos** - nincs confirmation dialog

**Fájlok**: `player.py:4126-4189`, `player.py:4315-4324`

### 5. Időtartam megjelenítés
- **Duration oszlop** a Start/Stop helyett
- **Total duration** összegzés minden cue lista alján
- **Automatikus számítás**: stop_at_sec - start_sec vagy full_duration - start_sec

**Fájlok**: `player.py:3897-3940`, `player.py:2421-2423`, `player.py:2657-2659`

### 6. Kép/PPT manuális stop
- **KRITIKUS FUNKCIÓ**: Képek és PPT prezentációk nem lépnek tovább automatikusan
- Az operátor teljes kontrollt kap - manuálisan kell leállítani
- **_handle_runner_finished()** módosítva: `if cue.kind in ("image", "ppt"): return`

**Fájlok**: `player.py:5296-5299`

### 7. Scene alapú munkafolyamat
- **ALL CUES eltávolítva** - minden médiának scene-hez kell tartoznia
- **Első scene automatikus kiválasztás** app indításkor
- **Első cue automatikus kiválasztás** scene aktiváláskor
- **Vizuális kiemelés**: aktív scene teljes színnel, inaktív szürke

**Fájlok**: `player.py:6358-6377`, `player.py:3064-3068`, `player.py:6334-6342`

### 8. Scene szerkesztő párbeszédablak
- **"Assign Cues" gomb eltávolítva** - most automatikus
- **Info label hozzáadva**: "Media is automatically assigned to this scene when you add it."
- **Notes textarea** megnövelve

**Fájlok**: `player.py:6549-6552`

## 📦 Függőségek

```bash
pip3 install pyobjc-framework-Quartz  # macOS iPad extended display support
```

Lásd: `requirements.txt`

## 🔧 Használat

### Screen Detection
1. Csatlakoztasd az iPad-et extended display-ként
2. Menj Settings > Display fülre
3. Kattints "🔍 Detect Screens" gombra
4. A koordináták automatikusan beállítódnak (pl. -1080, 0)

### Preview használat
- Válassz ki egy cue-t a Deck A vagy B listából
- A preview ablak automatikusan frissül
- Videó/kép esetén thumbnail látszik
- Audio/PPT esetén ikon + fájlnév

### Fekete képernyő
- Automatikusan megjelenik app indításkor a second screen-en
- Bezáródik amikor media kezd játszani
- Újra megjelenik ha nincs aktív playback

### Checkbox törlés
1. Kattints **Remove** gombra → checkboxok megjelennek
2. Kattints a törölni kívánt cue-k checkboxaira
3. Kattints **Remove** gombra újra → kiválasztottak törlődnek

## 🎯 Live Operation Best Practices

1. **Scene-alapú szervezés**: Minden médiát scene-ekbe rendezz
2. **Preview ellenőrzés**: Mindig nézd meg a preview-t lejátszás előtt
3. **Manuális kontroll**: Képek/PPT nem lépnek tovább automatikusan - te döntöd el mikor
4. **Fekete képernyő**: Biztonságos - a második képernyő fekete marad amíg nem küldesz ki médiát

## 🐛 Hibaelhárítás

### iPad nem detektálódik
- Ellenőrizd hogy az iPad extended display módban van (nem tükrözés!)
- Kattints "Detect Screens" gombra újra
- Nézd meg a Log fület a részletekért

### Preview nem jelenik meg
- Ellenőrizd hogy ffmpeg telepítve van
- Nézd meg a Log fület a hibákért
- Csak videó/kép esetén jelenik meg thumbnail

### Fekete képernyő nem jelenik meg
- Ellenőrizd a second_screen_left/top beállításokat
- Használd a "Detect Screens" funkciót
- Nézd meg a Log fület

## 📝 Technikai részletek

### Preview generálás
- **Threading**: Aszinkron működés, nem blokkolja a UI-t
- **FFmpeg**: Frame extraction (`-ss 1 -vframes 1`)
- **PIL/Pillow**: Méretezés aspect ratio megtartással
- **PhotoImage tárolás**: Garbage collection ellen védve

### Screen Detection
- **Quartz CGDisplayBounds**: Pontos koordináták minden képernyőhöz
- **CGMainDisplayID**: Fő képernyő azonosítás
- **CGGetActiveDisplayList**: Összes aktív display listázása
- **Negatív koordináták támogatása**: iPad left-side placement

### BlackScreenWindow
- **Singleton pattern**: Mindig csak egy példány
- **Toplevel window**: Független ablak
- **Fullscreen + overrideredirect**: Tiszta fekete kimenet
- **Escape billentyű**: Kézi bezárás lehetősége
