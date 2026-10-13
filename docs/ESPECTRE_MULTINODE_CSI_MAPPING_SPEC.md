# ESPectre Multi-Node CSI Motion Mapping

## Megvalósítási cél, architektúra és fejlesztési terv

**Állapot:** tervezési specifikáció. A terv nem állítja, hogy a
többérzékelős lokalizáció már bizonyítottan működik.

## 1. Végső cél

Hat ESP32-S3 panelből olyan helyi, Wi-Fi CSI-alapú mozgásérzékelő
rendszert építünk, amely ESPectre-adatokat gyűjt, időben összeilleszt,
érzékelőnként kalibrál, majd egy PC-s felületen megmutatja, hogy a
szobában mely zónákban valószínű a mozgás. Később, kalibrált tesztadatok
alapján, relatív pozícióbecslést is készíthet.

A fejlesztés sorrendje:

1.  Egy ESP adatfolyamának stabil fogadása.
2.  Két ESP adatainak összehasonlítása közös idővonalon.
3.  Hat érzékelő megbízható adatgyűjtése.
4.  Felülnézeti szobatérkép és érzékelőpozíciók.
5.  Helyszínspecifikus kalibráció és zónabecslés.
6.  Relatív koordináta becslése, ha a tesztek ezt indokolják.
7.  Nyers CSI-alapú kutatás, ha az ESPectre mozgáspontszáma nem elég a
    kívánt eredményhez.

**Első mérföldkő:** legalább két ESP adata stabilan, egyedi azonosítóval
és időbélyeggel jelenjen meg a PC-n; kieső adat esetén a felület
`STALE`/`OFFLINE` állapotot mutasson, ne nulla mozgást.

## 2. Fontos realitás: a mozgásdetektálás nem háromszögelés

Az ESPectre a CSI-ben megfigyelt rádiós változásokból mozgásállapotot és
mozgási pontszámot számol. A `motion_score` önmagában **nem
távolságmérés, nem szögmérés, és nem geometriai koordináta**. Három
érzékelő magas pontszáma nem jelenti azt, hogy a mozgó személy a három
érzékelő metszéspontjában van.

A mérésre hatással van többek között az AP és az ESP-k helye, a falak és
bútorok, a mozgás iránya/sebessége, a Wi-Fi-forgalom, a csatorna és az
érzékelőnkénti kalibráció.

Három külön funkciót kezeljünk:

-   **Mozgásdetektálás:** történt-e jelentős rádiós változás az érzékelő
    környezetében?
-   **Zónabecslés:** melyik előre kalibrált szobazónában a
    legvalószínűbb a mozgás?
-   **Relatív pozícióbecslés:** a kalibrációs minták alapján hol lehet a
    mozgás?

Az első verzió célja a zónabecslés legyen. Csak mérhető teszteredmények
után nevezzük a későbbi funkciót pozícióbecslésnek. Klasszikus
trilaterációhoz olyan validált, helyfüggő mérés kellene, amelyből
távolság vagy más geometriai információ következik; a mozgáspontszám
önmagában nem ilyen adat.

## 3. Hardver és felelősségek

-   **ESP 1--6:** ESPectre-alapú érzékelők.
-   **Wi-Fi AP:** stabil, lehetőleg állandó helyű 2,4 GHz-es hozzáférési
    pont.
-   **PC:** adatgyűjtés, közös idővonal, kalibráció, térkép,
    lokalizáció, naplózás.
-   **Forgalomforrás:** az ESPectre konfigurációjának megfelelő,
    dokumentált Wi-Fi-forgalom.

Ne kezdjünk rögtön hat panellel. Két eszköz elegendő az adatprotokoll és
az időillesztés teszteléséhez.

### Firmware-feladatok

Minden ESP:

1.  elindítja a Wi-Fi-t és az ESPectre runtime-ot;
2.  megvárja a kalibráció és a `ready_to_publish` állapotot;
3.  időbélyegzett, azonosítható telemetriát küld;
4.  jelzi a kapcsolat- és kalibrációs állapotát;
5.  újracsatlakozik, ha szükséges;
6.  nem jelenti mozgásmentesnek a környezetet pusztán azért, mert nincs
    friss adat.

### PC-feladatok

-   üzenetek fogadása és validálása;
-   időbélyegek összeillesztése;
-   eszközállapot és adatfrissesség figyelése;
-   érzékelőnkénti idősorok megjelenítése;
-   szobatérkép és érzékelőpozíciók kezelése;
-   kalibrációs felvételek rögzítése és címkézése;
-   zónabecslés, bizonytalanság és adatexport.

## 4. ESPectre-integráció

Mivel a böngészős ESPectre telepítő és monitor már működik, azt
referenciaként meg kell őrizni.

1.  Jegyezzük fel a működő firmware/SDK pontos verzióját és
    konfigurációját.
2.  Mentsünk egy alapfelvételt nyugalomról és mozgásról.
3.  Derítsük ki, milyen dokumentált API- vagy telemetriai mezőket
    biztosít az adott verzió.
4.  Egyetlen ESP-n teszteljük a saját adatgyűjtőt.
5.  Csak utána készítsünk saját firmware-integrációt.

Hivatalos SDK-útmutató:
https://github.com/francescopace/espectre/blob/main/docs/SDK.md

Az ESPectre dokumentációja ESP-IDF 5.5.3 vagy újabb verziót és C++17-et
ír le. A CSI konfigurációban szükséges lehet a
`CONFIG_ESP_WIFI_CSI_ENABLED=y`. A pontos követelményeket mindig a
kiválasztott release dokumentációjában ellenőrizzük.

Az SDK életciklusában fontos a Wi-Fi megfelelő inicializálása, a
`setup()` hibájának ellenőrzése, a `loop()` rendszeres futtatása,
valamint annak ellenőrzése, hogy az érzékelés kész-e adat publikálására.
Kalibráció vagy Wi-Fi-újracsatlakozás alatt az adatok átmenetileg nem
feltétlenül használhatók.

### Integrációs döntés

**Elsőként** a működő ESPectre firmware dokumentált adatfelületét
használjuk, ha a szükséges mozgás- és állapotmezők elérhetők.

**Másodikként**, ha több kontroll vagy hiányzó mezők miatt szükséges,
készítsünk saját ESP-IDF firmware-t az ESPectre C++ SDK-val. Ne
módosítsuk rögtön a CSI-feldolgozás belső részeit.

## 5. Telemetria és adatmodell

Javasolt útvonal:

`ESPectre → Wi-Fi → PC adatgyűjtő → közös idővonal → normalizálás → zónabecslés → térképes nézet`

Az ESPectre CSI-hez használt forgalmát és a PC-nek küldött telemetriát
logikailag külön kezeljük. A telemetria ne terhelje túl a hálózatot, és
ne változtassa meg észrevétlenül az érzékeléshez használt forgalmi
körülményeket.

A saját PC-s program belső adatsémája például:

``` json
{
  "schema_version": 1,
  "device_id": "sensor-01",
  "boot_id": "unique-per-boot",
  "sequence": 1024,
  "device_time_ms": 823456,
  "pc_receive_time_ms": 18345678,
  "ready": true,
  "calibrated": true,
  "motion_detected": true,
  "motion_score": 0.78,
  "threshold": 0.42,
  "wifi_connected": true
}
```

Ez **belső javasolt séma**, nem állítás arról, hogy az ESPectre pontosan
ezeket a mezőneveket használja. A tényleges adatokat az adott verzió
dokumentált API-jából kell leképezni. A példabeli számok szemléltető
értékek, nem ajánlott küszöbök.

Lehetséges adatátviteli utak:

-   ESPectre Direct HTTP/SSE, ha az adott buildben engedélyezett és
    elegendő mezőt ad;
-   MQTT, ha a firmware-ben engedélyezett és fut broker;
-   saját HTTP/UDP telemetria saját ESP-IDF firmware esetén.

Ne feltételezzük, hogy az ESP-NOW a legjobb telemetriai út. Az ESPectre
maga is Wi-Fi-forgalomra támaszkodik; először a kész SDK által
támogatott kommunikációs módot próbáljuk ki. Az ESP-NOW később külön
vezérlőcsatorna lehet, ha erre szükség van.

## 6. Időbélyegek és szinkronizálás

A lokalizációhoz nem elég az egyes érzékelők utolsó értékeit
összehasonlítani: az adatokat hasonló időablakból kell venni.

Minden mintához lehetőleg tároljuk:

-   az eszköz monoton idejét, amely az adott booton belül rendezhető;
-   a PC fogadási idejét;
-   az eszköz `boot_id` és `sequence` értékét.

Az ESP-k monoton órái nem hasonlíthatók közvetlenül egymáshoz. A PC
fogadási ideje hálózati késleltetést tartalmaz, ezért az összeillesztés
csak közelítő lehet. A későbbi, pontosabb megoldás becsülheti az
eszközórák eltérését és a szállítási késleltetést.

Kezdeti módszer:

1.  A PC az adatokat 100--250 ms-os kísérleti ablakokba csoportosítja.
2.  Érvényes mintákból ablakonként reprezentatív értéket számít.
3.  Csak friss, kalibrált adatot használ.
4.  Jelzi, ha valamelyik érzékelő hiányzik az ablakból.
5.  Az eredeti mintákat is elmenti későbbi elemzéshez.

A 100--250 ms csak kiinduló kísérleti tartomány; a tényleges frissítési
gyakoriság és késleltetés alapján kell módosítani. Az ESPectre pontszáma
eleve belső időablakos feldolgozásból származhat, tehát az adatok
összeillesztése nem teszi a rendszert fázisszinkron radarrá.

## 7. Térkép és érzékelőelhelyezés

A PC-n legyen egyszerű 2D felülnézeti térkép. A felhasználó adja meg a
szoba méretét és helyezze el az érzékelőket. Minden eszközhöz tároljunk:

-   `device_id`;
-   `x`, `y` koordináta;
-   opcionális orientáció;
-   kalibrációs állapot;
-   online/stale/offline állapot.

A térképen külön jelenjen meg:

1.  az érzékelő fizikai helye;
2.  az érzékelő saját mozgáspontszáma;
3.  a zónabecslés;
4.  a későbbi pozícióbecslés és annak bizonytalansága.

A szoba koordinátarendszere legyen rögzített, például bal felső sarok
`(0, 0)`, jobb alsó sarok `(width, height)`. A becsült folt vagy pont
vizualizáció, nem közvetlen távolságmérés.

Az érzékelők magassága, tájolása és az AP helye maradjon állandó a teszt
során. A legjobb elrendezést méréssel kell kiválasztani; nem garantált,
hogy a sarkokba helyezés optimális.

## 8. Lokalizáció lépésenként

### 8.1. Első szint: érzékelőnkénti mozgás

Minden ESP saját idősora jelenjen meg. Még nincs helybecslés. Mutassuk a
mozgási pontszámot, a kalibrációs állapotot, az adatfrissességet és az
elérhető CSI-diagnosztikát.

### 8.2. Második szint: zónabecslés

Osszuk a szobát 4--8 zónára. Egy személy ismert útvonalon mozogjon, és
minden szakaszt címkézzünk a megfelelő zónával. Több felvételt
készítsünk zónánként, eltérő iránnyal és sebességgel.

Kezdeti jellemzők lehetnek:

-   érzékelőnként normalizált mozgási pontszám;
-   rövid ablak átlagos és maximális pontszáma;
-   mozgásállapot-váltások száma;
-   több érzékelő együttes mintázata.

A becslés alapulhat egyszerű, kalibrált szabályokon vagy
mintahasonlóságon. A súlyokat és küszöböket a mért adatokból határozzuk
meg, ne találomra.

### 8.3. Harmadik szint: helyfüggő fingerprinting

Rögzítsünk mintákat ismert térképpontokon. Minden felvételhez mentsük a
pont koordinátáját, az összes érzékelő időbélyeges jellemzőit és a
környezet leírását.

A modell egy új mérést a kalibrált mintákhoz hasonlíthat, például
legközelebbi szomszéd módszerrel. A kalibrációs felvételek egy része
legyen tanítóadat, más része külön tesztadat. Ugyanabban a helyiségben
és elrendezésben mérjünk, ahol a modellt használjuk. Bútorok,
érzékelőhelyek vagy rádiós beállítások jelentős változása
újrakalibrációt tehet szükségessé.

### 8.4. Negyedik szint: nyers CSI

Csak akkor térjünk át nyers CSI-re, ha a mozgáspontszámokból nem érhető
el megfelelő zónabecslés. Ehhez külön adatgyűjtés, időbélyegzés,
minőségellenőrzés, jellemzőképzés és modellteszt szükséges. A nyers CSI
sem garantál pontos lokalizációt.

**A háromszögelés csak akkor valós cél**, ha sikerül validált, helyfüggő
geometriai információt kinyerni. A `motion_score` nem távolság, ezért
abból önmagában nem számolható korrekt trilateráció.

## 9. Szoftverarchitektúra

A mozgásdetektálást és a lokalizációt külön komponens kezelje.

### Motion layer

Bemenet: ESPectre mozgásállapot, pontszám, kalibráció és adatfrissesség.

Kimenet: normalizált érzékelőnkénti idősor, érvényességi állapot és
mozgásjelzés.

### Localization layer

Bemenet: időben összeillesztett érzékelőjellemzők, érzékelőpozíciók,
kalibrációs adatok és minőségi jelzések.

Kimenet: becsült zóna/koordináta, bizonytalanság, felhasznált érzékelők
száma és érvényesség.

Ha nincs elég érvényes adat, a rendszer `INSUFFICIENT DATA` vagy
`LOW CONFIDENCE` állapotot mutasson, ne találjon ki koordinátát.

## 10. PC-s alkalmazás

### Live Dashboard

-   érzékelőlista, online/stale/offline és kalibrációs állapot;
-   mozgási pontszám és idősor;
-   adatfrissesség, mintagyakoriság és kimaradás;
-   közös idővonalas összehasonlítás.

### Map View

-   szobaméret és érzékelők elhelyezése;
-   zónák rajzolása;
-   becsült mozgási zóna;
-   bizonytalanság;
-   offline eszközök jelzése.

### Calibration Recorder

-   felvétel indítása/leállítása;
-   ismert zóna vagy koordináta kiválasztása;
-   felvételek címkézése;
-   tanító- és tesztfelvételek elkülönítése;
-   CSV/JSON export.

### Diagnostics

-   telemetriai gyakoriság és késleltetés;
-   adatkimaradás és ismétlődő üzenetek;
-   ESPectre CSI-diagnosztika, ha elérhető;
-   kalibrációs állapotváltozások;
-   opcionális nyers CSI-rögzítés.

Ajánlott fájlok: - `telemetry.csv` -- időbélyeges eszközadatok; -
`labels.csv` -- kézzel megadott hely-/zónacímkék; - `localization.csv`
-- becslés és bizonytalanság; - `session.json` -- firmware-verziók,
konfiguráció, érzékelőhelyek és tesztleírás.

## 11. Normalizálás és adatminőség

Az érzékelők mozgáspontszáma nem feltétlenül közvetlenül
összehasonlítható. Minden érzékelőt külön kalibráljunk nyugalmi
állapotban, majd ismert mozgásokkal. A normalizálást a rögzített
kalibrációs adatokból vezessük le, és az eredeti értékeket is őrizzük
meg.

A hiányzó adatot ne helyettesítsük nullával: a nulla lehet valós
mozgásmérés, a hiányzó adat viszont nem mérés. Minden érzékelőnek legyen
konfigurálható elavulási határa. Ha a határ átlépődik, az érzékelő
stale/offline, és a lokalizáció bizonytalansága nő.

## 12. Tesztterv és elfogadási feltételek

### P0 -- Referencia

-   Működő böngészős firmware/SDK verzió és konfiguráció rögzítése.
-   Nyugalmi és mozgási alapfelvételek készítése.
-   A dokumentált telemetriai mezők és frissítési gyakoriság
    feljegyzése.

**Elfogadás:** ismert helyzetben reprodukálható mozgásdetektálás.

### P1 -- Egy ESP és PC

-   Dokumentált telemetria fogadása.
-   Grafikon és státusz megjelenítése.
-   Elavult adat felismerése.

**Elfogadás:** 30 perces próba alatt a kliens nem fagy le, az adatok
időbélyegesek és az adatkimaradás látható.

### P2 -- Két ESP

-   Két külön azonosítóval futó érzékelő.
-   Közös idővonal.
-   Az egyik kikapcsolása nem állítja le a másikat.

**Elfogadás:** a két adatfolyam elkülönül, újraindulás és kiesés
felismerhető.

### P3 -- Hat ESP

-   Minden eszköz csatlakozik és kalibrálódik.
-   Mért telemetriai gyakoriság, késleltetés és kimaradás.
-   A hálózati forgalom hatásának ellenőrzése.

**Elfogadás:** hat eszközzel is stabil adatgyűjtés és helyes
hibaállapotok.

### P4 -- Zónabecslés

-   4--8 zóna kijelölése.
-   Több felvétel minden zónában.
-   Tanító- és tesztadatok elkülönítése.
-   Pontosság és hibamátrix mérése.

**Elfogadás:** a becslés jobb a véletlenszerű zónaválasztásnál, és az
eredmény számszerűen dokumentált.

### P5 -- Relatív pozíció

-   Sűrűbb kalibrációs pontok.
-   Különböző mozgási irányok és sebességek.
-   Valós és becsült pozíció összehasonlítása.
-   Átlagos és medián helyhiba számítása.

**Elfogadás:** a helyhiba és a bizonytalanság dokumentált; pontossági
állítás csak elkülönített tesztadatok alapján tehető.

### P6 -- Nyers CSI, ha szükséges

-   Nyers CSI rögzítése.
-   Mintaminőség és kimaradások elemzése.
-   Jellemzőképzés és külön modellteszt.

**Elfogadás:** a nyers CSI-alapú módszer mérhetően javítja a
zónabecslést a korábbi modellhez képest.

## 13. Kockázatok

-   Hat érzékelő nem feltétlenül jelent hat független információforrást.
-   A Wi-Fi-forgalom és az AP beállításai hatással lehetnek a CSI-alapú
    érzékelésre.
-   A kalibráció helyszínfüggő, a bútorok és az eszközök mozgatása
    újrakalibrációt igényelhet.
-   A rendszer rádiós környezeti változást érzékel; nem
    személyazonosító, nem garantált személyszámláló, és nem életvédelmi
    vagy biztonsági rendszer.
-   Az ESPectre projekt GPL-3.0 licencű, és külön kereskedelmi licencet
    is említ. Terjesztés előtt ellenőrizni kell az adott verzió és
    integráció licencfeltételeit.

## 14. Hivatalos referenciák

-   ESPectre repository: https://github.com/francescopace/espectre
-   SDK: https://github.com/francescopace/espectre/blob/main/docs/SDK.md
-   CSI adatgyűjtés és forgalom:
    https://github.com/francescopace/espectre/blob/main/docs/CSI.md
-   API/protokoll:
    https://github.com/francescopace/espectre/blob/main/docs/API.md
-   README és korlátozások:
    https://github.com/francescopace/espectre/blob/main/README.md

Mindig a kiválasztott ESPectre-release dokumentációját használd; a
`main` ág változhat.

## Végső megvalósítási cél

A végső rendszer hat ESP32-S3-ból álló, helyi Wi-Fi CSI mozgásérzékelő
platform. Az ESPectre-adatokat időben összeilleszti, kalibrálja és
térképen jeleníti meg. Először zónabecslést készít, később pedig csak
mért és tesztelt eredmények alapján próbál relatív koordinátát becsülni.

A rendszernek világosan meg kell mutatnia: 1. mely érzékelők mikor
észleltek mozgást; 2. mely zónában valószínű a mozgás; 3. milyen
bizonytalansággal készült a becslés; 4. mennyit javít a több érzékelő a
becslésen; 5. indokolt-e saját, nyers CSI-alapú algoritmus fejlesztése.

**Vezérelv: előbb stabil adatgyűjtés, aztán mérhető zónabecslés, és csak
utána relatív pozícióbecslés. Ne nevezzünk háromszögelésnek olyan
eredményt, amelyet valójában csak kalibrált mozgási pontszámokból
becsültünk.**
