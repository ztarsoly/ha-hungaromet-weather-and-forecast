# HungaroMet Weather & Forecast – Home Assistant integráció

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/v/release/ztarsoly/ha-hungaromet-weather-and-forecast)](https://github.com/ztarsoly/ha-hungaromet-weather-and-forecast/releases)

Ez az egyéni Home Assistant integráció a [HungaroMet Magyar Meteorológiai Szolgálat](https://www.met.hu/) nyilvános adataiból ad időjárás-érzékelőket, előrejelzést és radarképeket.

## Funkciók

- **Mért időjárás**: a helyszínhez közeli állomások napi, órás és 10 perces mérései (hőmérséklet, csapadék, légnyomás, szél, páratartalom, sugárzás, talajhőmérséklet és más adatok), a kiválasztott állomások átlagaként
- **Állomások**: mely állomások mérései adják az értékeket
- **Előrejelzés**: időjárás-entitás 7 napos és órás előrejelzéssel egy magyarországi településre, valamint előrejelzés-érzékelők, riasztás és figyelmeztetés
- **Radarképek**: animált országos radarkép, és helyszínenként a környék regionális radarképe
- **Több helyszín**: az integráció többször is hozzáadható, mindegyik helyszín saját szolgáltatásokkal
- **Választható szolgáltatások**: a mérőállomás-szolgáltatások, a regionális és az országos radarkép külön-külön kikapcsolhatók

## Követelmények

- Home Assistant 2024.12 vagy újabb
- Internetkapcsolat az `odp.met.hu` és a `www.met.hu` eléréséhez

Külön Python-csomagot nem kell telepíteni: az integráció csak a Python és a Home Assistant saját csomagjait használja.

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

1. Töltse le a `hungaromet_weather_and_forecast-<verzió>.zip` fájlt a [legutóbbi kiadásból](https://github.com/ztarsoly/ha-hungaromet-weather-and-forecast/releases/latest)
2. Csomagolja ki a Home Assistant konfigurációs könyvtárában (ahol a `configuration.yaml` van) lévő `custom_components` könyvtárba; ha nincs ilyen könyvtár, hozza létre. A végén a `custom_components/hungaromet_weather_and_forecast/manifest.json` fájlnak kell meglennie
3. Indítsa újra a Home Assistantet

Frissítéshez törölje a `custom_components/hungaromet_weather_and_forecast` könyvtárat, csomagolja ki ugyanígy az új kiadás zip-fájlját, és indítsa újra a Home Assistantet.

## Beállítás

Az integráció a felhasználói felületen állítható be:

1. Lépjen a Beállítások → Eszközök és szolgáltatások menübe
2. Kattintson az „Integráció hozzáadása” gombra
3. Keressen rá a „HungaroMet Weather & Forecast” integrációra
4. Töltse ki a mezőket:

| Mező | Jelentés |
|---|---|
| **Név** | A helyszín neve; a szolgáltatások nevének eleje lesz (pl. `Pécs` → *Pécs Napi Időjárás*). Csak akkor maradhat üresen, ha kizárólag a radart veszi fel. |
| **Állomáskeresési sugár (km)** | A helytől ennyi kilométeren belüli állomások átlagát adják az érzékelők (1–100 km, alapértelmezés: 20 km). Az adat nélküli (nem elérhető) állomásokat nem veszi figyelembe. Ha egy elérhető állomás sincs ilyen közel, a legközelebbi elérhető állomás adatait használja. |
| **Előrejelzés települése** | Nem kötelező. Egy magyarországi település (pl. `Debrecen`) az előrejelzéshez, ékezetekkel, helyesen írva; a nevet a met.hu-n keresi meg az integráció. Üresen hagyva nincs előrejelzés. |
| **Hely** | Honnan mérje az állomáskeresési sugarat, és a regionális radarkép az ország melyik negyedét mutassa: a Home Assistant otthoni helye (**Otthon**) vagy az előrejelzés települése (**Előrejelzés települése**) alapján |
| **Szolgáltatások** | A *Napi időjárás*, *Órás időjárás*, *10 perces időjárás* szolgáltatás és a *Regionális radarkép* közül a kívántak (alapértelmezés: mind). Csak a bejelöltek töltődnek le és jönnek létre. Legalább egyet be kell jelölni, vagy meg kell adni az előrejelzés települését. |
| **Országos radarkép** | Jelölőnégyzet, amely csak akkor látszik, ha még nincs **Radar** bejegyzés; alapból bejelölve, és ekkor felveszi |

Mentés előtt az integráció ellenőrzi, hogy a met.hu ismeri-e a települést, és hogy a választott források letölthetők-e: mérőállomás-szolgáltatás választásakor a HungaroMet mérései, csak regionális radarkép választásakor a radarkép. Ha csak a radart veszi fel, a legújabb országos radarképkocka letöltését ellenőrzi. Ha valamelyik nem sikerül, az űrlap hibát jelez.

Hogy a bejegyzések ne ismételjék egymás érzékelőit, minden előrejelzés-település csak egy bejegyzésben szerepelhet, és csak egy bejegyzés szolgáltatásai mérhetnek az **Otthon** helyen (az előrejelzés nélküli bejegyzés is ott mér; a csak előrejelzést adó, mérőállomás és regionális radarkép nélküli bejegyzés nem számít ide); az állomáskeresési sugár ebből a szempontból nem számít. A további bejegyzésekhez ezért másik települést kell megadni, és az **Előrejelzés települése** helyet kell választani. Az ezt sértő beállítást a hozzáadás és a **Konfigurálás** űrlapja is elutasítja. A korábbi verzióval felvett, egymást átfedő bejegyzések tovább működnek, és a többi beállításuk módosítható. Egymáshoz közeli helyeknél az állomásérzékelők és a regionális radarkép ettől még egyezhetnek.

A név, a sugár, a település, a hely és a szolgáltatások később is módosíthatók a bejegyzés **Konfigurálás** gombjával. Ha törli egy szolgáltatás jelölését, a szolgáltatás és az entitásai törlődnek; ha kiüríti az előrejelzés települését, ugyanígy törlődik az *Előrejelzés* szolgáltatás. Új névnél a bejegyzés szolgáltatásai is az új nevet kapják; az Ön által átnevezett szolgáltatások nevében csak a régi név cserélődik. Ugyanez történik, ha a bejegyzést a Home Assistant saját **Átnevezés** menüpontjával nevezi át.

### Több helyszín

Az integrációt többször is hozzáadhatja, például az otthonára és egy másik településre. Minden bejegyzés saját szolgáltatásokat kap, a bejegyzés **Név** mezője szerint elnevezve: a `HungaroMet` nevű bejegyzés szolgáltatása a *HungaroMet Napi Időjárás*, a `Pécs` nevűé a *Pécs Napi Időjárás*, és így tovább.

Az országos radarkép mindenhol ugyanaz, ezért saját bejegyzése van, a **Radar**. Amíg nincs ilyen bejegyzés, a beállítás űrlapján megjelenik az **Országos radarkép** jelölőnégyzet (alapból bejelölve); bejelölve a helyszínnel együtt a **Radar** bejegyzés is létrejön. Ha csak a radart szeretné, hagyja üresen a nevet és az előrejelzés települését, törölje minden szolgáltatás jelölését, a jelölőnégyzet pedig maradjon bejelölve. Ha a **Radar** bejegyzést törli, magától nem jön létre újra, a jelölőnégyzet viszont ismét megjelenik, amikor új bejegyzést ad hozzá. Kikapcsolásához tiltsa le a bejegyzést.

## Eltávolítás

1. Lépjen a Beállítások → Eszközök és szolgáltatások → **HungaroMet Weather & Forecast** oldalra
2. Törölje a helyszínek bejegyzéseit (a bejegyzés hárompontos menüjében: **Törlés**), utoljára pedig a **Radar** bejegyzést. A bejegyzéssel együtt a szolgáltatásai, az entitásai és a mentett előrejelzése is törlődnek
3. Távolítsa el az integrációt: a HACS-ben a hárompontos menü **Eltávolítás** (Remove) pontjával, kézi telepítés esetén pedig a `custom_components/hungaromet_weather_and_forecast` könyvtár törlésével
4. Indítsa újra a Home Assistantet

Az entitások korábbi előzményei a Home Assistant előzmény-megőrzési beállításai szerint törlődnek.

## Szolgáltatások és entitások

Helyszínenként (a mérőállomás-szolgáltatások és a regionális radarkép közül csak a bejelöltek):

| Szolgáltatás | Tartalom |
|---|---|
| *{Név} Napi Időjárás* | Az előző nap mérései |
| *{Név} Órás Időjárás* | Az utolsó óra mérései |
| *{Név} 10 Perces Időjárás* | Az utolsó 10 perc mérései |
| *{Név} Előrejelzés* | Időjárás-entitás és előrejelzés-érzékelők (csak ha megadott települést) |
| *{Név} Regionális Radarkép* | Az ország azon negyedének (északnyugat, északkelet, délnyugat vagy délkelet) radarképe, amely a helyszínt lefedi |

A **Radar** bejegyzésben: *Országos Radarkép*.

### Mért időjárás

A napi, órás és 10 perces szolgáltatások érzékelői a kiválasztott állomások méréseinek átlagát mutatják (a szélirányoknál irányhelyes átlagot). Az elérhető mérések szolgáltatásonként eltérnek:

| Mérés | Napi | Órás | 10 perces |
|---|:-:|:-:|:-:|
| Mérés ideje | ✓ | ✓ | ✓ |
| Hőmérséklet (átlag, minimum, maximum) | ✓ | ✓ | ✓ |
| Pillanatnyi hőmérséklet | | ✓ | ✓ |
| Felszínközeli minimumhőmérséklet | ✓ | ✓ | ✓ |
| Talajhőmérséklet 5, 10, 20, 50 és 100 cm-en | ✓ | ✓ | ✓ |
| Vízhőmérséklet | ✓ | ✓ | ✓ |
| Csapadék | ✓ | ✓ | ✓ |
| Párolgás | ✓ | | |
| Relatív páratartalom | ✓ | ✓ | ✓ |
| Műszerszinti légnyomás | ✓ | ✓ | ✓ |
| Tengerszinti légnyomás | ✓ | ✓ | |
| Szélsebesség | ✓ | ✓ | ✓ |
| Szélirány | | ✓ | ✓ |
| Szinoptikus szélsebesség | ✓ | ✓ | |
| Szinoptikus szélirány | | ✓ | |
| Uralkodó szélirány (égtáj) | ✓ | | |
| Maximális széllökés sebessége, iránya és ideje | ✓ | ✓ | ✓ |
| Globálsugárzás összesen (J/cm²) | ✓ | ✓ | |
| Globálsugárzás, 10 perces átlag (W/m²) | | | ✓ |
| UV-sugárzás összesen (MED) | ✓ | ✓ | |
| UV-sugárzás, 10 perces átlag (MED/h) | | | ✓ |
| Gammadózis | ✓ | ✓ | ✓ |
| Látótávolság | ✓ | ✓ | ✓ |
| Időkép kódja | | ✓ | |

Mindhárom szolgáltatásnak van egy **Állomások** érzékelője is. Az értéke a felhasznált állomások száma, a `stations` attribútuma az állomások listája (szám, név, koordináták, tengerszint feletti magasság), a legközelebbivel kezdve, a `raw_data` attribútuma pedig az állomások teljes mérési sorai, a távolságukkal együtt.

### Előrejelzés

Az előrejelzés szolgáltatás ezeket adja:

- A szolgáltatásról elnevezett **időjárás-entitást** napi (7 napos) és órás előrejelzéssel az időjárás-kártyákhoz
- **Érzékelőket** az aktuális órára: hőmérséklet, csapadék, felhőzet, szélsebesség, széllökés, szélirány (fok és égtáj), szélerősség (Beaufort-skála, a szélsebességből számítva), légnyomás és időjárási helyzet
- A **mai minimum- és maximumhőmérsékletet**, valamint az előrejelzés **kiadásának idejét**
- A **Riasztás 3 órán belül** és a **Figyelmeztetés mára** érzékelőt a HungaroMet veszélyjelzéséből

A **Riasztás 3 órán belül** érzékelő a település járására érvényben lévő riasztást, a **Figyelmeztetés mára** érzékelő a település megyéjére a mai napra kiadott figyelmeztetést mutatja. Az értékük a legmagasabb érvényben lévő fokozat: *Nincs*, *Sárga*, *Narancs* vagy *Piros* (pl. *Sárga riasztás*). Az `alerts`, illetve a `warnings` attribútumuk az érvényben lévő jelenségek listája, a legmagasabb fokozattal kezdve; ha nincs érvényben semmi, a lista üres. Minden elemnek három adata van: `label` (a jelenség neve a Home Assistant nyelvén: magyarul, ha az magyar, egyébként angolul), `type` (a jelenség nyelvtől független kulcsa) és `level` (`yellow`, `orange` vagy `red`). Például:

```yaml
warnings:
  - label: Széllökés
    type: wind_gust
    level: orange
  - label: Zivatar
    type: thunderstorm
    level: yellow
```

| Jelenség | `type` | Riasztás | Figyelmeztetés |
|---|---|---|---|
| Zivatar | `thunderstorm` | ✓ | ✓ |
| Széllökés | `wind_gust` | ✓ | ✓ |
| Felhőszakadás | `cloudburst` | ✓ | ✓ |
| Ónos eső | `freezing_rain` | ✓ | ✓ |
| Hófúvás | `snowdrift` | ✓ | ✓ |
| Eső | `rain` | | ✓ |
| Havazás | `snowfall` | | ✓ |
| Tartós, sűrű köd | `fog` | | ✓ |
| Extrém hideg | `extreme_cold` | | ✓ |
| Magas középhőmérséklet | `high_mean_temperature` | | ✓ |

A járást a település koordinátái alapján az integráció a met.hu veszélyjelzési térképének járáshatáraiból határozza meg (a térkép adatai az integrációban vannak); Budapest a Pest megyei figyelmeztetést kapja. A mai napot a veszélyjelzés napjai közül a nap neve alapján választja ki, így éjfél után is a mai figyelmeztetést mutatja, bár a met.hu csak 00:00 UTC-kor (magyar idő szerint 01:00-kor, nyáron 02:00-kor) lép a következő napra.

A met.hu 6 óránként (01, 07, 13 és 19 órakor) ad egy előrejelzési sort, amely az időpontjától kezdődő 6 órát írja le. A köztes órák számítottak: a hőmérséklet, a felhőzet, a szélsebesség, a széllökés, a légnyomás és a szélirány az egyik sortól a következőig egyenletesen változik (2 tizedesre kerekítve), az időjárási helyzet és a csapadék viszont a sor teljes időszakára vonatkozik, a csapadék egyenlően elosztva az órák között. Ezek számított értékek, nem külön előrejelzések.

A met.hu a sorokat 4 órával az időpontjuk után leveszi az oldalról. Az integráció a mai nap korábbi sorait megtartja, hogy a mai minimum és maximum egész nap helyes maradjon.

### Radarképek

Mindkét radarkép a legutóbbi 6 képkocka (30 perc) animációja: képkockánként 1 másodperc, az utolsón 2 másodperc. Minden frissítéskor csak az új képkocka töltődik le, a legrégebbi kiesik. Az animáció csak a memóriában van, a lemezre nem kerül. Ha a listázott képkockák bármelyike nem tölthető le, a kép nem elérhető, amíg mind meg nem érkezik.

## Frissítés

Az időpontok UTC szerintiek (télen a magyar idő UTC+1, nyáron UTC+2).

| Adat | Mikor | Sikertelen letöltés vagy még régi adat esetén |
|---|---|---|
| Napi időjárás | Naponta 09:35:59-kor | óránként újra |
| Órás időjárás | Óránként, xx:20:59-kor | 10 percenként újra |
| 10 perces időjárás | 10 percenként, xx:x0:59-kor | percenként újra |
| Előrejelzés | Óránként, xx:00:59-kor | 10 percenként újra |
| Riasztás és figyelmeztetés | Óránként, xx:00:59-kor | 10 percenként újra |
| Radarképek | 5 percenként, xx:x0:59-kor és xx:x5:59-kor | percenként újra |

Az újrapróbálkozás legfeljebb a következő ütemezett frissítésig tart. Ezen felül:

- Indításkor és a bejegyzés újratöltésekor minden azonnal frissül. Ami ekkor nem sikerül, azt az integráció az ütemezett próbálkozások mellett percenként is újrapróbálja, amíg először nem sikerül.
- Ha ekkor egy helyszín egyetlen mérése, előrejelzése, riasztása és figyelmeztetése sem tölthető le, és nincs az aktuális órát tartalmazó mentett előrejelzése, akkor a bejegyzés nem töltődik be. A Home Assistant újrapróbálja a beállítását, néhány másodperc után egyre ritkábban, végül 10 percenként; addig a bejegyzés entitásai (a regionális radarkép is) nem elérhetők. Mérőállomás-szolgáltatás nélküli bejegyzés akkor is betöltődik, ha csak a választott regionális radarképe tölthető le.
- A **Radar** bejegyzés sem töltődik be, amíg a legújabb országos radarképkocka nem tölthető le. A Home Assistant ugyanígy újrapróbálja a beállítását, az okot az integráció oldalán mutatja, és addig az *Országos Radarkép* nem elérhető.
- A `hungaromet_weather_and_forecast.update` művelet azonnal frissít mindent: minden bejegyzés időjárás-érzékelőit, előrejelzését, riasztását és figyelmeztetését, valamint a regionális és az országos radarképet. Paramétere nincs. Ha egy bejegyzés sincs betöltve, hibát jelez. Egy-egy entitás a Home Assistant `homeassistant.update_entity` műveletével is frissíthető.
- A letiltott entitások adatait az ütemezett frissítések és a fenti műveletek nem töltik le. Indításkor és a bejegyzés újratöltésekor viszont minden választott mérőállomás-szolgáltatás mérései egyszer letöltődnek, a **Radar** bejegyzés pedig letölti a legújabb országos radarképkockát akkor is, ha az érintett entitások le vannak tiltva. Ha ekkor egyik mérés sem tölthető le, és nincs az aktuális órát tartalmazó mentett előrejelzés, az integráció az előrejelzést, majd szükség esetén a riasztást is megpróbálja letölteni.
- Ha a bejegyzés rendszerbeállításaiban kikapcsolja a frissítések lekérdezését (*Enable polling for updates*), az ütemezett frissítések és a sikertelen letöltések automatikus újrapróbálásai elmaradnak. Az indításkori és újratöltéskori egyszeri frissítés, valamint a fenti műveletek ilyenkor is működnek, és a be nem töltött bejegyzés beállítását a Home Assistant ilyenkor is újrapróbálja.

## Nem elérhető entitások

Az érzékelők, az időjárás-entitás és a radarképek *ismeretlen* érték és elavult adat helyett **nem elérhetők** (unavailable):

- ha a legutóbbi letöltésük nem sikerült, a következő sikeres letöltésig (az előrejelzés kivételével, lásd lent);
- ha a sikeres letöltésben nincs értékük, például az állomások nem mérik az adott mennyiséget, vagy a met.hu időjárás-leírása ismeretlen (ilyenkor az adott órának nincs időjárási helyzete);
- ha az előrejelzés nem tartalmazza az aktuális órát. Ez az első telepítés után fordulhat elő, legfeljebb a következő előrejelzési sor időpontjáig. Ilyenkor az időjárás-entitás előrejelzése sem érhető el.

**Kivétel az előrejelzés:** ha a letöltése nem sikerül, az előrejelzés-érzékelők és az időjárás-entitás az utolsó letöltött (újraindítás után a mentett) előrejelzést követik óráról órára. Csak akkor nem elérhetők, ha abban már nincs benne az aktuális óra. A riasztás és a figyelmeztetés ilyenkor is nem elérhető.

A sikertelen letöltést az integráció egyszer, figyelmeztetésként (WARNING) írja a naplóba, a következő sikeres letöltést pedig szintén egyszer. A közbenső sikertelen próbálkozások nem kerülnek a naplóba. Váratlan hibánál a figyelmeztetés a hiba részleteit (traceback) is tartalmazza. Az ismeretlen időjárás-leírásról és szélirányról bejegyzésenként és értékenként egy figyelmeztetés kerül a naplóba, és egy arról is, ha a megadott távolságon belül nincs mérő állomás, ezért a legközelebbit használja. Ha egy bejegyzés újratöltődik (például a beállításai módosítása után), vagy a Home Assistant újraindul, a naplózás elölről kezdődik: a még mindig sikertelen letöltés újra bekerül a naplóba, a korábban sikertelen, majd az újratöltés után rögtön sikeres letöltésről pedig nem készül külön bejegyzés. Ha a bejegyzés nem töltődik be, és a Home Assistant később újrapróbálja a beállítását, ezekről a sikertelen letöltésekről csak hibakeresési (DEBUG) szintű naplóbejegyzés készül: az okot a Home Assistant mutatja az integráció oldalán, és a sikeres betöltés után nem készül „works again” bejegyzés.

## Adatforrások

| Adat | Forrás |
|---|---|
| Napi, órás és 10 perces mérések | A HungaroMet nyílt adatai: [odp.met.hu](https://odp.met.hu/weather/weather_reports/synoptic/hungary/) (tömörített CSV-fájlok, minden állomással) |
| Országos radarkép | [odp.met.hu radar-kompozit](https://odp.met.hu/weather/radar/composite/png/refl2D_pscappi/) (PNG) |
| Regionális radarkép | A met.hu [radaroldala](https://www.met.hu/idojaras/aktualis_idojaras/radar/) (a negyedek csapadékintenzitás-képei) |
| Előrejelzés | A met.hu [települési előrejelzése](https://www.met.hu/idojaras/elorejelzes/magyarorszagi_telepulesek/) |
| Riasztás és figyelmeztetés | A met.hu [veszélyjelzése](https://www.met.hu/idojaras/veszelyjelzes/) |

A regionális radarkép, az előrejelzés, a riasztás és a figyelmeztetés a met.hu nyilvános weboldalairól származik, így az oldalak változása elronthatja őket.

## Tárolt adatok

Az integráció a Home Assistant `.storage` mappájába csak a legutóbbi előrejelzést menti (`hungaromet_weather_and_forecast.forecast.<bejegyzés azonosító>`). A mentett előrejelzés pótolja egy sikeres letöltésből hiányzó, a met.hu oldaláról már levett sorokat, és ez látszik, amíg az újraindítás utáni letöltés nem sikerül. A bejegyzés törlésekor ez a fájl is törlődik.

A riasztás, a figyelmeztetés, az állomási mérések és a radarképek nincsenek mentve. A korábbi verziók riasztás- és figyelmeztetésfájlját az integráció törli.

## Köszönetnyilvánítás

Fabian Gabor [HA-HungaroMet](https://github.com/FabianGabor/HA-HungaroMet) integrációján alapul.

## Licenc

Az integráció az [MIT licenc](LICENSE) alatt érhető el.
