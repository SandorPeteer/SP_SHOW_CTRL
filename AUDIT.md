# SP Show Control · Windows üzemi audit

A vizsgálat a csomagolt Show Control belépési pontját követte a vezérlőfelülettől az mpv IPC-n, a médiakezelésen, a PowerPoint-exporton és a show mentésén át a csomagban lévő eszközökig. A külön indítható yt-dlr alkalmazás és a régi kísérleti lejátszók külön belépési pontok.

| Terület | Megállapítás és módosítás |
| --- | --- |
| Indítás | Az mpv IPC-kapcsolat és a show fájl beolvasása várakoztathatta a Tk eseménykezelését. Indításkor mindkettő háttérszálon fut, látható állapottal. A Windows-mutex megfogja a dupla indítást. |
| Lejátszás közbeni frissítés | A kezelőszál korábban újra és újra lekérte az mpv lejátszási pozícióját és a klip végét. Az mpv most változáskor küldi az állapotot. A rajzolás és a dialoop ebből olvas. |
| IPC-hiba | Az írás és válaszvárás közös időkeretet kapott. Timeout után rövid szünet fogja meg az ismétlődő várakozást. A visszautasított fájl hibát jelez. |
| Háttérmunka | A média hosszát végig egy worker méri, ugyanarra az útvonalra egyszerre egy méréssel. A hullámforma és az előnézet saját, korlátozott workerrel fut. A hosszabb műveleteket az állapotsáv jelzi. |
| PowerPoint | A képek és MP4-ek soros előkészítőn készülnek. Az export a forrás ujjlenyomatával, ellenőrzött manifesttel kerül a gyorsítótárba. Az előkészített diát saját mpv-kimenet vetíti. |
| Tárolás | A mentés a műsor pillanatképével dolgozik. Tartalom szerint deduplikál, ellenőrzi a másolt médiát, és a végén atomikusan cseréli a show JSON-fájlt. |
| Felület | A három listában a görgetésjelző rárajzolódik a saját keskeny peremére. A fájlműveletek, az állapotjelző és az ablakvezérlők egy fejlécben vannak. Az ablak natív módon méretezhető. Váratlan Tk callback-hiba a státuszsorba és a naplóba kerül, így a hibaüzenet mellett is vezérelhető marad a műsor. |

## A 2026.11.18-i kezelési modell

- Az Új műsor megerősítése a Qt-ablakon belül jelenik meg; a fájl- és színválasztók, valamint a külön eszközablakok a kezelőablakhoz tartoznak. Az Escape/Cancel visszaadja a fókuszt. A Qt és a lejátszó worker közötti adatátvitel nem várakoztatja a Qt eseménykezelését; kapcsolatvesztéskor látható hibajelzés marad.
- A kijelölés, a következő indítandó elem és a futó média külön állapot. A kézzel előkészített elem az indítás/folytatás célpontja, a görgetési helyet az idő- és állapotfrissítések megőrzik. Másik jelenet korábbi lejátszásának vége nem indíthat elemet az új jelenetben.
- Az On screen és a Next slide teljes panelje és képterülete egyforma, a vízszintes VU a hullámforma alatt van. A belső keretek és hátterek megszűntek. Az Audio only kapcsoló közvetlenül az Audio/Video fejlécben van. A visszaszámláló a végéhez közel állandó borostyán jelzést kap; a hullámforma lejátszott szakaszának sötétítése megmarad.
- A folyamatos hullámforma a dekódolt PCM tényleges pozitív/negatív kitéréseiből készül. Megőrzi a csendet, az egymáshoz viszonyított amplitúdókat és a rövid csúcsokat; frekvenciaszínezése külön FFT-elemzésből származik.
- Alapmódban egyetlen hangforrás és egyetlen VU-kijelzés van. A prezentáció hangja A-t a valódi lejátszási pozíción szünetelteti; a dia vége nem folytatja A-t. Kézi A Play visszaigazolt B-némítás után indul.
- Az explicit Mix kapcsoló önmagában nem indít el vagy folytat médiát. Mixben A/B külön mérője látható; ezek nem alkotnak összegzett master-mérőt. Mix OFF az utoljára kézzel indított forrást tartja meg, és csak sikeres pause/mute után vált alapmódra. A Mix nem mentett műsorbeállítás.

## A szintmérés jelentése

A mérő az élő mpv dekóder szűrés és kimeneti csatorna-átalakítás utáni RMS- és sample-peak értékét mutatja, az mpv szoftveres hangerővel és némítással korrigálva. Nem true-peak és nem a hangkártya fizikai kimenetének visszamérése. Hiányzó vagy elavult telemetriából nem készül becsült érték. A csatornakiosztás változása és a lejátszó újraindítása alatt a mérő érvénytelen adatot nem publikál.

Az ismert PCM-jellel végzett helyi próba a valódi mpv-kimenethez hasonlítja a szinteket. A 5.1→sztereó átalakítás normalizálás nélkül mintánként azonos; normalizálással float32 kimeneten szintén azonos, s16 kimeneten a megengedett és mért legnagyobb eltérés 1 LSB (1/32768, körülbelül −90,3 dBFS).

## Az üzemi próba határai

- A Tk kezelőszálon maradt néhány közvetlen kezelői művelet, például a fullscreen mód váltása és az mpv parancsok kiadása. Egy válaszkésés ezeknél továbbra is okozhat rövid megtorpanást.
- A PowerPoint COM és a GPU-meghajtó belső működését a Show Control nem vezérli. A beragadt Office-hívás feltarthatja a további PPT-k előkészítését. Az előkészített kivetítés külön mpv-folyamatban fut.
- A Windows CI valódi csomagolt EXE-t és mpv-kimenetet tesztel virtuális asztalon, előkészített PPT-médiával. A fizikai projektor, a PowerPoint-telepítés, a prezentáló és egy konkrét videó dekódolása külön hardveres próba.
