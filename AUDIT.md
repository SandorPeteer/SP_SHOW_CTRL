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
| Felület | A három listában a görgetésjelző rárajzolódik a saját keskeny peremére. A natív ablakvezérlők sötét címsorban működnek. Váratlan Tk callback-hiba a státuszsorba és a naplóba kerül, így a hibaüzenet mellett is vezérelhető marad a műsor. |

## Az üzemi próba határai

- A Tk kezelőszálon maradt néhány közvetlen kezelői művelet, például a fullscreen mód váltása és az mpv parancsok kiadása. Egy válaszkésés ezeknél továbbra is okozhat rövid megtorpanást.
- A PowerPoint COM és a GPU-meghajtó belső működését a Show Control nem vezérli. A beragadt Office-hívás feltarthatja a további PPT-k előkészítését. Az előkészített kivetítés külön mpv-folyamatban fut.
- A Windows CI valódi csomagolt EXE-t és mpv-kimenetet tesztel virtuális asztalon, előkészített PPT-médiával. A fizikai projektor, az Excel/PowerPoint-telepítés, a prezentáló és egy konkrét videó dekódolása külön hardveres próba.
