# HungaroMet Weather & Forecast – Home Assistant integráció

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ztarsoly/ha-hungaromet-weather-and-forecast)](https://github.com/ztarsoly/ha-hungaromet-weather-and-forecast/releases)

Ez az egyéni Home Assistant integráció a HungaroMet Magyar Meteorológiai Szolgálat nyilvános adataiból ad időjárás-érzékelőket, előrejelzést és radarképeket.

## Funkciók

- **Mért időjárás**: a helyszínhez közeli állomások napi, órás és 10 perces mérései (hőmérséklet, csapadék, légnyomás, szél, páratartalom, sugárzás, talajhőmérséklet és más adatok), a kiválasztott állomások átlagaként
- **Állomások**: mely állomások mérései adják az értékeket
- **Előrejelzés**: időjárás-entitás 7 napos és órás előrejelzéssel egy magyarországi településre, valamint előrejelzés-érzékelők
- **Radarképek**: animált országos radarkép, és helyszínenként a környék regionális radarképe
- **Több helyszín**: az integráció többször is hozzáadható, mindegyik helyszín saját eszközökkel

## Követelmények

- Home Assistant 2024.12 vagy újabb
- Internetkapcsolat az `odp.met.hu` és a `www.met.hu` eléréséhez

A szükséges Python-csomagokat (pandas, requests, Pillow, beautifulsoup4) a Home Assistant telepíti.

## Telepítés

### HACS (ajánlott)

1. Nyissa meg a HACS-t a Home Assistantben
2. Kattintson a jobb felső sarokban a három pontra
3. Válassza az „Egyéni tárolók” (Custom repositories) lehetőséget
4. Adja hozzá egyéni tárolóként: `https://github.com/ztarsoly/ha-hungaromet-weather-and-forecast`
5. Kategóriának válassza az „Integráció” lehetőséget
6. Kattintson a „Hozzáadás” gombra
7. Keressen rá a „HungaroMet Weather & Forecast” integrációra, és telepítse
8. Indítsa újra a Home Assistantet

### Kézi telepítés

1. Másolja a `custom_components/hungaromet_weather_and_forecast` mappát a Home Assistant `custom_components` könyvtárába
2. Indítsa újra a Home Assistantet

## Beállítás

Az integráció a felhasználói felületen állítható be:

1. Lépjen a Beállítások → Eszközök és szolgáltatások menübe
2. Kattintson az „Integráció hozzáadása” gombra
3. Keressen rá a „HungaroMet Weather & Forecast” integrációra
4. Töltse ki a mezőket:

| Mező | Jelentés |
|---|---|
| **Név** | A helyszín neve; az eszközök nevének eleje lesz (pl. `Pécs` → *Pécs Napi Időjárás*) |
| **Állomáskeresési sugár (km)** | A helytől ennyi kilométeren belüli állomások átlagát adják az érzékelők (1–100 km, alapértelmezés: 20 km). Ha egy állomás sincs ilyen közel, a legközelebbi állomás adatait használja. |
| **Előrejelzés települése** | Nem kötelező. Egy magyarországi település (pl. `Debrecen`) az előrejelzéshez, ékezetekkel, helyesen írva; a nevet a met.hu-n keresi meg az integráció. Üresen hagyva nincs előrejelzés. |
| **Időjárás-érzékelők helye** | Honnan mérje az állomáskeresési sugarat: a Home Assistant otthoni helyétől (**Otthon**) vagy az előrejelzés településétől (**Előrejelzés települése**) |

A sugár, a település és a hely később is módosítható a bejegyzés **Konfigurálás** gombjával.

### Több helyszín

Az integrációt többször is hozzáadhatja, például az otthonára és egy másik településre. Minden bejegyzés saját eszközöket kap, a bejegyzés **Név** mezője szerint elnevezve: a `HungaroMet` nevű bejegyzés eszköze a *HungaroMet Napi Időjárás*, a `Pécs` nevűé a *Pécs Napi Időjárás*, és így tovább.

Az országos radarkép mindenhol ugyanaz, ezért saját bejegyzése van, a **Radar**. Az első helyszín hozzáadásakor automatikusan létrejön, és ha törli, a következő újraindításkor visszatér. Kikapcsolásához tiltsa le a bejegyzést.

## Eszközök és entitások

Helyszínenként:

| Eszköz | Tartalom |
|---|---|
| *{Név} Napi Időjárás* | Az előző nap mérései |
| *{Név} Órás Időjárás* | Az utolsó óra mérései |
| *{Név} 10 Perces Időjárás* | Az utolsó 10 perc mérései |
| *{Név} Előrejelzés* | Időjárás-entitás és előrejelzés-érzékelők (csak ha megadott települést) |
| *{Név} Regionális Radarkép* | Az ország azon negyedének (északnyugat, északkelet, délnyugat vagy délkelet) radarképe, amely a helyszínt lefedi |

A **Radar** bejegyzésben: *Országos Radarkép*.

### Mért időjárás

A napi, órás és 10 perces eszközök érzékelői a kiválasztott állomások méréseinek átlagát mutatják (a szélirányoknál irányhelyes átlagot). Az elérhető mérések eszközönként eltérnek:

| Mérés | Napi | Órás | 10 perces |
|---|:-:|:-:|:-:|
| Mérési időpont | ✓ | ✓ | ✓ |
| Hőmérséklet (átlag, minimum, maximum) | ✓ | ✓ | ✓ |
| Pillanatnyi hőmérséklet | | ✓ | ✓ |
| Felszínközeli hőmérséklet minimuma | ✓ | ✓ | ✓ |
| Talajhőmérséklet 5, 10, 20, 50 és 100 cm-en | ✓ | ✓ | ✓ |
| Vízhőmérséklet | ✓ | ✓ | ✓ |
| Csapadékösszeg | ✓ | ✓ | ✓ |
| Párolgás, vízegyenleg | ✓ | | |
| Relatív nedvesség | ✓ | ✓ | ✓ |
| Műszerszinti légnyomás | ✓ | ✓ | ✓ |
| Tengerszinti légnyomás | ✓ | ✓ | |
| Átlagos szélsebesség | ✓ | ✓ | ✓ |
| Átlagos szélirány | | ✓ | ✓ |
| Szinoptikus szélsebesség | ✓ | ✓ | |
| Szinoptikus szélirány | | ✓ | |
| Uralkodó szélirány (égtáj) | ✓ | | |
| Maximális széllökés sebessége, iránya és ideje | ✓ | ✓ | ✓ |
| Globálsugárzás összeg (J/cm² és MJ/m²) | ✓ | ✓ | ✓ |
| UV-sugárzás összeg | ✓ | ✓ | ✓ |
| Gammadózis | ✓ | ✓ | ✓ |
| Látástávolság | ✓ | ✓ | ✓ |
| Időkép kódja | | ✓ | |

Mindhárom eszköznek van egy **Állomások** érzékelője is. Az értéke a felhasznált állomások száma, a `stations` attribútuma az állomások listája (szám, név, koordináták, tengerszint feletti magasság), a legközelebbivel kezdve, a `raw_data` attribútuma pedig az állomások teljes mérési sorai, a távolságukkal együtt.

### Előrejelzés

Az előrejelzés eszköz ezeket adja:

- Az eszközről elnevezett **időjárás-entitást** napi (7 napos) és órás előrejelzéssel az időjárás-kártyákhoz
- **Érzékelőket** az aktuális órára: hőmérséklet, csapadék, felhőzet, szélsebesség, széllökés, szélirány (fok és égtáj), légnyomás és időjárási helyzet
- A **mai minimum- és maximumhőmérsékletet**, valamint az előrejelzés **kiadásának idejét**

A met.hu 6 óránként (01, 07, 13 és 19 órakor) ad egy előrejelzési sort, amely az időpontjától kezdődő 6 órát írja le. A köztes órák számítottak: a hőmérséklet, a felhőzet, a szélsebesség, a széllökés, a légnyomás és a szélirány az egyik sortól a következőig egyenletesen változik (2 tizedesre kerekítve), az időjárási helyzet és a csapadék viszont a sor teljes időszakára vonatkozik, a csapadék egyenlően elosztva az órák között. Ezek számított értékek, nem külön előrejelzések. Az előrejelzés-érzékelők `forecast` attribútuma a met.hu eredeti 6 órás sorait tartalmazza.

A met.hu a sorokat 4 órával az időpontjuk után leveszi az oldalról. Az integráció a mai nap korábbi sorait megtartja, hogy a mai minimum és maximum egész nap helyes maradjon.

### Radarképek

Mindkét radarkép a legutóbbi 5 képkocka (25 perc) animációja: képkockánként 1 másodperc, az utolsón 2 másodperc. Minden frissítéskor csak az új képkocka töltődik le, a legrégebbi kiesik. Az animáció csak a memóriában van, a lemezre nem kerül.

## Frissítés

Az időpontok UTC szerintiek (télen a magyar idő UTC+1, nyáron UTC+2).

| Adat | Mikor | Sikertelen letöltés vagy még régi adat esetén |
|---|---|---|
| Napi időjárás | Naponta 09:35:59-kor | óránként újra |
| Órás időjárás | Óránként, xx:20:59-kor | 10 percenként újra |
| 10 perces időjárás | 10 percenként, xx:x0:59-kor | percenként újra |
| Előrejelzés | Óránként, xx:00:00-kor | 10 percenként újra |
| Radarképek | 5 percenként, xx:x0:59-kor és xx:x5:59-kor | percenként újra |

Az újrapróbálkozás legfeljebb a következő ütemezett frissítésig tart. Ezen felül:

- Indításkor és a bejegyzés újratöltésekor minden azonnal frissül.
- A `hungaromet_weather_and_forecast.update` szolgáltatás azonnal frissít mindent: minden bejegyzés időjárás-érzékelőit és előrejelzését, valamint a regionális és az országos radarképet. Egy-egy entitás a Home Assistant `homeassistant.update_entity` szolgáltatásával is frissíthető.
- A letiltott entitások adatai nem töltődnek le.
- Ha a bejegyzés rendszerbeállításaiban kikapcsolja a frissítések lekérdezését (*Enable polling for updates*), az ütemezett frissítések elmaradnak; a fenti szolgáltatások ilyenkor is működnek.

## Adatforrások

| Adat | Forrás |
|---|---|
| Napi, órás és 10 perces mérések | A HungaroMet nyílt adatai: [odp.met.hu](https://odp.met.hu/weather/weather_reports/synoptic/hungary/) (tömörített CSV-fájlok, minden állomással) |
| Országos radarkép | [odp.met.hu radar-kompozit](https://odp.met.hu/weather/radar/composite/png/refl2D_pscappi/) (PNG) |
| Regionális radarkép | A met.hu [radaroldala](https://www.met.hu/idojaras/aktualis_idojaras/radar/) (a negyedek csapadékintenzitás-képei) |
| Előrejelzés | A met.hu [települési előrejelzése](https://www.met.hu/idojaras/elorejelzes/magyarorszagi_telepulesek/) |

A regionális radarkép és az előrejelzés a met.hu nyilvános weboldalairól származik, így az oldalak változása elronthatja őket.

## Tárolt adatok

Az integráció csak a legutóbbi előrejelzést menti a Home Assistant `.storage` mappájába (`hungaromet_weather_and_forecast.forecast.<bejegyzés azonosító>`), hogy újraindítás után is legyen előrejelzés, és a mai nap már levett sorai megmaradjanak. A bejegyzés törlésekor ez a fájl is törlődik.

## Köszönetnyilvánítás

Fabian Gabor [HA-HungaroMet](https://github.com/FabianGabor/HA-HungaroMet) integrációján alapul.

## Licenc

Az integráció az [MIT licenc](LICENSE) alatt érhető el.
