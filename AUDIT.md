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

- Az Új műsor megerősítése a Qt-ablakon belül jelenik meg; a fájl- és színválasztók, valamint a külön eszközablakok a kezelőablakhoz tartoznak. Az Escape/Cancel, illetve az eszközablak bezárása visszaadja a fókuszt. A natív Windows-tulajdonost a GW_OWNER szerinti kapcsolat igazolja. A Qt és a lejátszó worker közötti adatátvitel nem várakoztatja a Qt eseménykezelését; kapcsolatvesztéskor látható hibajelzés marad.
- A kijelölés, a következő indítandó elem és a futó média külön állapot. A kézzel előkészített elem az indítás/folytatás célpontja, a görgetési helyet az idő- és állapotfrissítések megőrzik. Másik jelenet korábbi lejátszásának vége nem indíthat elemet az új jelenetben.
- A Page Up/Page Down a Qt által elfogadott PgUp/PgDown gyorsbillentyűnevekkel működik; valódi billentyűeseményes regresszió ellenőrzi az előre/hátra léptetést és az inaktív prezentáció tiltását.
- Az On screen és a Next slide teljes panelje és képterülete egyforma, a vízszintes VU a hullámforma alatt van. A belső keretek és hátterek megszűntek. Az Audio only kapcsoló közvetlenül az Audio/Video fejlécben van. A visszaszámláló a végéhez közel állandó borostyán jelzést kap; a hullámforma lejátszott szakaszának sötétítése megmarad.
- A folyamatos hullámforma a dekódolt PCM tényleges pozitív/negatív kitéréseiből készül. Megőrzi a csendet, az egymáshoz viszonyított amplitúdókat és a rövid csúcsokat; frekvenciaszínezése külön FFT-elemzésből származik.
- Alapmódban egyetlen hangforrás és egyetlen VU-kijelzés van. A prezentáció hangja A-t a valódi lejátszási pozíción szünetelteti; a dia vége nem folytatja A-t. Kézi A Play visszaigazolt B-némítás után indul.
- A kézi A Play miatti átmeneti dianémítás megőrzi az operátor Slide audio beállítását. Új, kézzel indított prezentáció ezt visszaállítja; az automatikus dialéptetés nem veheti vissza magától a hangot.
- Az explicit Mix kapcsoló önmagában nem indít el vagy folytat médiát. Mixben A/B külön mérője látható; ezek nem alkotnak összegzett master-mérőt. Mix OFF az utoljára kézzel indított forrást tartja meg, és csak sikeres pause/mute után vált alapmódra. A Mix nem mentett műsorbeállítás.

## A szintmérés jelentése

A mérő az élő mpv dekóder szűrés és kimeneti csatorna-átalakítás utáni RMS- és sample-peak értékét mutatja, az mpv szoftveres hangerővel és némítással korrigálva. Nem true-peak és nem a hangkártya fizikai kimenetének visszamérése. Hiányzó vagy elavult telemetriából nem készül becsült érték. A csatornakiosztás változása és a lejátszó újraindítása alatt a mérő érvénytelen adatot nem publikál.

A VU kitöltése közvetlenül követi az aktuális mérést. A telemetria által újraindított szélességanimáció korábban kisebb kitérést rajzolhatott; ezt folyamatos, 33 ms-onként változó adatokkal végzett mono/sztereó és normál/Mix regresszió ellenőrzi.

Az ismert PCM-jellel végzett helyi próba a valódi mpv-kimenethez hasonlítja a szinteket. A 5.1→sztereó átalakítás normalizálás nélkül mintánként azonos; normalizálással float32 kimeneten szintén azonos, s16 kimeneten a megengedett és mért legnagyobb eltérés 1 LSB (1/32768, körülbelül −90,3 dBFS).

## Projection kezelési kiegészítések — 2026.11.18

- A PPT sorának „Slide interval…” menüje prezentációnként menti az automatikus diaváltás idejét (0,5–3600 másodperc). A sor kiírja a beállított időt, és külön jelzi az éppen futó Loop-léptetést. Egyedi érték nélkül a korábbi exportált diánkénti időzítés marad érvényben („Slide timing”); a videódia a saját végéig fut. Az idő szerkesztése nem indítja el a prezentációt és nem kapcsolja be a Loopot.
- A Projection sor jobb gombos „Use as Still” menüje képet, videót vagy PPT-t jelölhet ki műsoronkénti visszatérő médiának. A megjelölés nem indít lejátszást; az aktív elemre ismételt Show, a Stop és a következő automatikus elem nélküli videóvég visszatér erre. A készre választott sor és a görgetés megmarad. A Still a jelenlegi Repeat és Sound kapcsolót követi, nem folytatja A-t; Repeat OFF mellett a videó végképe megmarad, újraindulási hurok nélkül. Új műsor, betöltés és bezárás nem indít Stillt. A hivatkozás a hordozható műsorral együtt mentődik.
- A Projection fejlécének Sound on/off kapcsolója a közvetlen videófájlokra és az előkészített PPT videóira egyaránt érvényes. Alapmódban visszaigazolt hangátadás, Mixben egyidejű lejátszás működik. A némítás nem indítja újra a képet és nem folytatja A-t. Az automatikus videóváltás megtartja a hangengedélyt, és nem írja felül a kézi hangátvételt.
- A Projection lejátszási sávja a dekóder valódi pozícióját, az eltelt és hátralévő időt mutatja. A tekerés az aktuális IN/OUT tartományon belül, ugyanabban a lejátszóban történik. A kattintás/húzás rögzíti a cue és fájl azonosítóját, ezért egy időközben indult másik videót nem tekerhet el. A hang-, Mix- és szünetállapot megmarad.
- A végkockán bekapcsolt Sound csak a hangengedélyt állítja be, A-t nem szünetelteti. Ha az operátor ezután futó állapotba teker vissza, előbb visszaigazolt hangátadás történik; Mixben mindkét forrás tovább futhat. Sikertelen hangátadásnál a tekerés nem indul el.
- A Play/Show színe a lejátszó által igazolt futást követi; kijelölés vagy gombnyomás nem kapcsolja be előre. A néma videó és a szándékosan kitartott állókép is aktív lehet. A folyamatos A-óra megfigyelője nem tulajdonosa a folyamatnak: annak életét a MediaRunner ellenőrzi, a megjelenítést a tényleges IPC-telemetria vezérli.
- A két Hozzáadás ablak közös utolsó médiamappát jegyez meg a meglévő felhasználói settings.json-ban. A Mégse és a műsorfájl megnyitása/mentése nem írja át; a mappa nem kerül bele a hordozható show beállításaiba. Nem elérhető mappánál az alapértelmezett hely nyílik meg.
- A fekete videónyitás előnézete legfeljebb az első öt másodpercben, másodpercenként két mintaképből keresi az első látható képet. A keresés megszakítható, a meglévő időkorlátot használja; a lejátszási kezdőpontot és a kifejezetten megadott diaposztert nem módosítja.

## Az üzemi próba határai

- A Tk kezelőszálon maradt néhány közvetlen kezelői művelet, például a fullscreen mód váltása és az mpv parancsok kiadása. Egy válaszkésés ezeknél továbbra is okozhat rövid megtorpanást.
- A PowerPoint COM és a GPU-meghajtó belső működését a Show Control nem vezérli. A beragadt Office-hívás feltarthatja a további PPT-k előkészítését. Az előkészített kivetítés külön mpv-folyamatban fut.
- A Windows CI valódi csomagolt EXE-t és mpv-kimenetet tesztel virtuális asztalon, előkészített PPT-médiával. A fizikai projektor, a PowerPoint-telepítés, a prezentáló és egy konkrét videó dekódolása külön hardveres próba.

## Ellenőrzött Windows-csomag — 2026.11.18

A `f28d6fe` alkalmazáskód [Windows-próbája](https://github.com/SandorPeteer/SP_SHOW_CTRL/actions/runs/44239775379) minden ellenőrzési kapun átment. A csomagolt EXE valódi Windows-billentyűkkel végrehajtott próbája 31 ellenőrzést rögzített: az eszközablakok tulajdonosa, fókusza és méretezése; Új/Megtartás/Mégse; Megnyitás/Mentés másként/Hozzáadás; hordozható mentés és újraindítás utáni visszatöltés. A fájlablak bezárásakor korábban elvesző következő fájlparancs most egyszer, a lezárás után fut le. Lejátszási vagy törlési parancs nem kerül ilyen késleltetett sorba.

A külön Qt- és mpv-próba igazolta a megjelenő hullámformát és VU-t, a szünet/folytatást, a kézi hangváltást, a két valós lejátszóórával futó Mixet, a diahang-beállítás visszaállítását, a képarányokat és a lapozóbillentyűket. A képernyőképek vizuális ellenőrzése megtörtént. A virtuális gép hangkártya nélkül, explicit null hangkimenettel tesztel; ez nem fizikai hangpróba.

A végső aláírásjegyzék 264 binárist tartalmaz: 227 érvényesen aláírt, 37 aláíratlan. A csomag nem teljesen kiadói aláírású; a Smart App Control megfelelőség nincs megoldva. Windows-biztonsági beállítást nem módosítottunk.

## Újabb ellenőrzés — Projection és PPT-időköz

A `f7a2794` alkalmazáskód [44257225524 számú buildje](https://github.com/SandorPeteer/SP_SHOW_CTRL/actions/runs/44257225524) macOS-en és Windowson is sikeres. A Windows-kapukban 173 célzott teszt és 36 csomagolt EXE-s, natív felületellenőrzés futott le. A külön Qt- és mpv-próbák a közvetlen videó Sound/Mix működését, a valódi tekerést, az állapotfüggő Play/Show színeket, a Still visszatérését és megőrzését, a médiamappa megjegyzését és a PPT-nként mentett időközt is ellenőrizték.

A valódi Qt-menüből beírt 7,5 másodperc megjelent a PPT sorában, miközben a másik videó és a készre választott sor változatlan maradt. A 0,75 másodperces PPT-próbán nyolc váltás történt 0,750–0,813 másodperces időközökkel, két teljes körben; az eredeti exportált időzítések megmaradtak. A videótekerés ténylegesen 3,2-ről 1,3 másodpercre vitte a dekódert, 0,25 másodperces megfigyelt válaszidővel, a hangerő, némítás, Mix és A lejátszása változatlanul maradt. Ezek virtuális Windows-mérések, nem hardveres késleltetési garanciák.

A Repeat OFF Still a valódi EOF-t és a végkockát tartotta, A végig szünetelt. A teszt külön kezeli az utolsó képkocka idejének 3,9 → 4,0 másodperces beállását, de visszaugrást vagy újraindulást nem enged. A Windows-képernyőképeket vizuálisan ellenőriztük; a szűk fejléc szükség esetén két sorba rendezi a kezelőgombokat. Az Audio only továbbra is közvetlenül az Audio / Video fejlécében jelenik meg kijelölt videónál.

## Hordozható operátori tesztcsomag

A `scripts/build_operator_test_media.py` nyolc szintetikus médiafájlt állít elő: ismert szintű L/R PCM-minta, dinamikus hullámforma, 1080p/25 fps időkód és villanás–csippanás, egy másodperces fekete nyitás, monoszkóp videó és PNG, Still-logó, valamint hat másodperces végpróba. Két teljes generálás byte-azonos eredményt adott. A PCM csúcsok −18,00052 és −6,00048 dBFS körüliek; a dekódolt AAC-csippanások korrelációs időeltérése 0 minta a forráshoz képest, a 1024 mintás AAC-előfutás skip metadata alapján kompenzált. Ez fájlszintű ellenőrzés, nem fizikai kimeneti késésmérés vagy színkalibráció.

A `scripts/build_operator_test_show.py` hét jelenetet és 135 egyedi cue-t hoz létre, két 60 soros listával, külön IN/OUT- és fade-próbával, 2/5 másodperces PPT-cue-kkal. A valódi PPTX beágyazott MP4-e és az Office nélkül használható előkészített másolat byte-azonos. A tényleges show-betöltő és PPT-cache ellenőrzése sikeres, lejátszás/GUI helyett teszt-helyettesítőkkel; a kész ZIP-ből másik könyvtárba kibontva is sikeres volt. A Microsoft Office tényleges exportja külön operátori próba marad.

A magyar indítóleírás és a két A4-es oldalra ellenőrzött HTML-tesztlap forrása `packaging/operator-test-pack/`. A `scripts/package_operator_test_pack.py` az ellenőrzött `f7a2794` Windows-buildhez csatolja a 19 tesztfájlt. Csomagolás után a ZIP CRC-je, minden tesztfájl SHA-256-ja és mind a 264 alkalmazásbináris CI-jegyzékkel való egyezése ellenőrzött. Az alkalmazás kódja e csomagolási lépéssel nem változik.
