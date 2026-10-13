# wifi_detection – Wi-Fi CSI mozgásérzékelés ESP32-S3 node-okkal

Többcsomópontos Wi-Fi CSI mozgásérzékelő rendszer, amely a hivatalos [ESPectre](https://github.com/francescopace/espectre) SDK-ra épül. Az ESP32-S3 node-ok a CSI-t helyben dolgozzák fel, a PC-s oldal a node-ok dokumentált Direct HTTP/SSE felületén gyűjti a telemetriát, naplózza, és élő dashboardon mutatja.

A hosszú távú terv a [docs/ESPECTRE_MULTINODE_CSI_MAPPING_SPEC.md](docs/ESPECTRE_MULTINODE_CSI_MAPPING_SPEC.md) fájlban van (P0–P6 fázisok).

## Állapot

| Fázis | Tartalom | Állapot |
|---|---|---|
| P0 | Hivatalos ESPectre firmware egy eszközön | kész |
| P1 | Egy node + PC-s telemetria, naplózás | kész |
| P2 | Két node egyszerre, élő dashboard | kész (a kiesés-teszt még nincs lefuttatva) |
| P3+ | Közös idővonal (ablakok), kalibrációs felvétel, zónatérkép | tervezett |

## Felépítés

```
firmware/
  idf.ps1            ESP-IDF wrapper (build / flash / monitor) Windowsra
  official/          ESPectre 3.0.0 `wifi_motion_detection` példa + a Direct HTTP bekötés
pc/
  collector.py       SSE gyűjtő, telemetry.csv + session.json
  dashboard.py       élő dashboard szervere (a collectorra épül)
  dashboard.html     dashboard felület (egy fájl, külső függőség nélkül)
docs/
  ESPECTRE_MULTINODE_CSI_MAPPING_SPEC.md   teljes terv
```

## Hardver és szoftver

- 2 × ESP32-S3 (ugyanaz a firmware mindkettőn), USB-n flashelve
- 2,4 GHz Wi-Fi, a node-ok és a PC ugyanabban a hálózatban
- ESP-IDF 5.5.3 vagy újabb (a fejlesztés 6.1.0-val történt), ESPectre SDK 3.0.0
- Python 3.9+ a PC-s oldalhoz, külső csomagok nélkül (csak a standard könyvtár)

## Firmware

A [firmware/official/main/app_main.cpp](firmware/official/main/app_main.cpp) a hivatalos példa. A CSI-feldolgozáshoz nem nyúltunk, csak a következők kerültek bele:

- a Direct HTTP/SSE szolgáltatás indítása a Wi-Fi csatlakozás után (port 62587, `/espectre/v1`)
- élő `motion` események publikálása és `sensing` állapotváltozás jelzése
- `CONFIG_ESPECTRE_SDK_ENABLE_DIRECT=y` az `sdkconfig.defaults`-ban

### Wi-Fi adatok megadása

A Wi-Fi SSID és jelszó **nincs a repóban**. Az `sdkconfig` tartalmazza őket, ezért a `.gitignore` kizárja. Megadás:

```powershell
.\firmware\idf.ps1 set-target esp32s3
.\firmware\idf.ps1 menuconfig      # ESPectre Wi-Fi beállítások
```

### Build és flash

A [firmware/idf.ps1](firmware/idf.ps1) fejlesztői gépre van beállítva: a benne lévő elérési utak (ESP-IDF, venv, toolchain) a saját gépre igazítandók. Az ESP-IDF 6.1.0 mellé az Espressif xtensa-esp-elf 15.2.0 toolchain kell. A PlatformIO régebbi toolchainje nem tartalmazza a `picolibc.specs`-et, és nem fordul vele.

```powershell
.\firmware\idf.ps1 build
.\firmware\idf.ps1 -p COM6 flash
.\firmware\idf.ps1 -p COM6 monitor
```

Az `espressif/mdns` függőséget a build tölti le. Ha az `ESPECTRE_SDK_ENABLE_DIRECT` kapcsolót utólag állítod át, futtass `reconfigure`-t.

## PC-s oldal

A node-ok IP-címe DHCP-ből jön. Az eszköz IP-jét a router eszközlistájából, vagy az ARP-táblából (az eszköz MAC-címe alapján) lehet megtudni, esetleg DHCP-foglalással érdemes rögzíteni.

### Élő dashboard

```powershell
cd pc
python dashboard.py 192.168.0.202 192.168.0.48
```

Megnyitás: <http://127.0.0.1:8080>. Nodeonként:

- élő grafikon az utolsó 60 másodpercről, küszöbvonallal és a mozgásos szakaszok jelölésével
- állapot: ONLINE, NOT_READY, STALE, OFFLINE; nyugalom / MOZGÁS / kalibrálás
- küszöb állítása (csúszka vagy szám) és újrakalibrálás gomb
- eszközinfó: device ID, firmware, RSSI, csatorna, uptime, minták, újracsatlakozások
- eseménynapló

A szerver csak a `127.0.0.1` címen hallgat, mert a node-ok API-ja nem kér hitelesítést.

### Csak naplózás

```powershell
python collector.py 192.168.0.202 192.168.0.48 --duration 1800
```

Minden futás egy `pc/sessions/<időbélyeg>/` mappát hoz létre `telemetry.csv` és `session.json` fájlokkal. A `telemetry.csv` mezői: `schema_version, device_id, boot_id, sequence, device_time_ms, pc_receive_time_ms, pc_receive_iso, ready, calibrated, motion_detected, motion_score, threshold, wifi_connected`.

- A `boot_id` és a `sequence` a PC-n képződik, mert az ESPectre API nem ad ilyet. Ha a node órája visszalép, új `boot_id` jön létre.
- Ha egy node 3 másodpercig nem küld adatot, STALE lesz, 10 másodperc után OFFLINE. A hiányzó adat sosem számít nulla mozgásnak.
- A kapcsolat megszakadásakor a gyűjtő automatikusan újracsatlakozik.

## Ismert korlátok

- A node-ok API-ja hitelesítés nélküli, ezért csak megbízható helyi hálózaton használható.
- Az SSE-n elmaradt események nem ismétlődnek, lassú kliensnél a `motion` események eldobódhatnak.
- Az SDK hivatalosan 5.5.x-et validálta, az ESP-IDF 6.x hardveres validációja még függőben van, ezen a hardveren eddig hibátlanul működik.
- A grafikon csak a dashboard megnyitása alatt gyűlő adatot mutat (utolsó 120 mp).

## Licenc

Az ESPectre GPL-3.0 licencű, a származékos firmware ezért szintén. A teljes szöveg a [LICENSE](LICENSE) fájlban van. Az ESPectre kereskedelmi licencet külön kínál.
