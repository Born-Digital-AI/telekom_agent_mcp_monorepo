# MCP Telekom Identity — biznis dokumentácia toolov

Dokumentácia pre biznis používateľov. Popisuje, **čo ktorý tool robí, kedy sa
používa a akú logiku má vo vnútri** — bez technických detailov implementácie.
Technická dokumentácia (API, testovacie dáta, spustenie) je v [README.md](README.md).

---

## Na čo služba slúži

Služba `mcp_telekom_identity` dáva AI asistentovi (chatbot / voicebot) schopnosť:

1. **Identifikovať zákazníka** — zistiť, kto je na druhej strane, podľa jedného
   údaja (telefónne číslo, rodné číslo, IČO, kód zákazníka, sériové číslo zariadenia).
2. **Overiť jeho totožnosť** (autentifikácia) — viacfaktorové overenie predtým,
   než asistent poskytne citlivé informácie alebo vykoná zmenu.
3. **Odpovedať na biznis otázky** — napr. dokedy má zákazník viazanosť.
4. *(voliteľne)* **Vyhľadávať v znalostnej báze** — odpovede na všeobecné otázky
   z interných dokumentov Telekomu.

Dáta o zákazníkoch číta zo systémov Telekomu cez DPS rozhrania (Party Management,
Customer Management, Product Inventory). Služba **iba číta** — nič v systémoch nemení.

### Typický priebeh konverzácie

```text
Zákazník: „Dokedy mám viazanosť?"
   1. identifikacia          → „Kto ste?" — nájde zákazníka podľa zadaného údaja
   2. autentifikacia         → „Naozaj ste to vy?" — overí 2 (resp. 3) faktory
   3. over_viazanost         → vráti stav viazanosti a odporúčanú odpoveď
```

V **chat kanáli** asistent zbiera údaje cez formuláre (widgety) — zákazník ich
vyplní priamo v okne chatu. V **hlasovom kanáli** sa pýta slovne. Dôležité:
údaje z formulárov **nikdy neprechádzajú cez jazykový model** — idú bezpečným
kanálom priamo do toolu (ochrana osobných údajov).

---

## Prehľad toolov

| Tool | Účel | Podmienka použitia |
| --- | --- | --- |
| `identifikacia` | Nájde zákazníka podľa jedného identifikačného údaja | — |
| `zobraz_identifikacny_widget` | Zobrazí identifikačný formulár v chate | len chat kanál |
| `autentifikacia` | Viacfaktorovo overí totožnosť zákazníka | po úspešnej identifikácii |
| `zobraz_autentifikacny_widget` | Zobrazí overovací formulár v chate | len chat kanál |
| `over_viazanost` | Zistí aktívne viazanosti (zmluvy) zákazníka | po identifikácii + autentifikácii |
| `nastav_test_kontext` | Len na testovanie — simuluje údaje z telefónnej ústredne/NLP | testovacie prostredie |
| `znalostna_baza_vyhladaj` | Sémantické vyhľadávanie v znalostnej báze | ak je znalostná báza zapnutá |
| `znalostna_baza_zoznam_dokumentov` | Katalóg dokumentov znalostnej bázy | ak je znalostná báza zapnutá |
| `znalostna_baza_detail_dokumentu` | Celý obsah jedného dokumentu | ak je znalostná báza zapnutá |
| `znalostna_baza_stitky` | Zoznam štítkov (kategórií) dokumentov | ak je znalostná báza zapnutá |

---

## 1. `identifikacia` — nájdenie zákazníka

**Jediný vstupný bod identifikácie.** Zákazník (alebo asistent) zadá jeden údaj
a tool **sám rozpozná, o aký typ údaja ide** — netreba vopred vedieť, či ide
o telefón alebo rodné číslo.

### Aké údaje prijíma

| Typ údaja | Príklad | Kto ho typicky používa |
| --- | --- | --- |
| Telefónne číslo | `0902 804 660`, `+421 902 804 660` | B2C zákazník |
| Rodné číslo | `7304292105` (bez lomky) | B2C zákazník |
| IČO | `86316923` (8 cifier) | firemný zákazník |
| Kód zákazníka / fakturačného účtu | `1002203200` (8–12 cifier, z faktúry) | B2C aj B2B |
| Sériové číslo zariadenia | `M91450EB0603` (router, set-top box, modem) | zákazník s TV/internetom |

### Ako tool rozpozná typ údaja (automatická klasifikácia)

Rozpoznávanie je založené na **štruktúre a kontrolných súčtoch**, nie na hádaní:

1. Obsahuje písmená → **sériové číslo**.
2. Začína `+421`, `00421` alebo `421` → **telefónne číslo**.
3. 8 cifier s platným kontrolným súčtom IČO → **IČO**.
4. 9–10 cifier s platným dátumom narodenia a deliteľnosťou 11 → **rodné číslo**.
5. Začína `0` a dá sa previesť na slovenské telefónne číslo → **telefónne číslo**.
6. Inak 8–12 cifier → **kód zákazníka / fakturačného účtu** (záchytná kategória).

Ak sa údaj dá vyložiť **viacerými spôsobmi** (napr. číslo začínajúce `09…` je
platné aj ako telefón, aj ako rodné číslo), tool nehľadá naslepo — v chate
požiada zákazníka, aby vybral typ údaja z ponuky (rozšírený formulár).

### Čo sa deje po rozpoznaní typu

| Typ | Logika vyhľadania |
| --- | --- |
| Rodné číslo | Vyhľadá osobu v evidencii osôb (Party Management) → k nej dohľadá zákaznícke kontá. Zosnulé a zrušené záznamy sa **vylúčia**. |
| IČO | Rovnako ako rodné číslo, ale hľadá firmu (organizáciu). |
| Kód zákazníka | Kód končiaci **0** = priamo zákaznícke číslo → načíta zákazníka. Kód končiaci **1–9** = číslo fakturačného účtu → cez faktúru dohľadá vlastníka. |
| Telefónne číslo | Vyhľadá **službu** s týmto číslom v inventári produktov → cez službu dohľadá zákazníka. (Telefón je zároveň zapamätaný ako overený kontakt — pomôže pri autentifikácii.) |
| Sériové číslo | Vyhľadá **zariadenie** v inventári produktov → cez zariadenie dohľadá zákazníka. |

### Možné výsledky

| Výsledok | Čo znamená | Čo nasleduje |
| --- | --- | --- |
| **Nájdený 1 zákazník** | Tool vráti meno zákazníka (napr. „Stano Muziková"). | Asistent pokračuje autentifikáciou. |
| **Viac zhôd** | Rovnaký údaj má v systéme viac záznamov. Tool vráti zoznam mien. | Treba vyžiadať presnejší údaj — autentifikácia s nejednoznačnou identifikáciou nie je možná. |
| **Nenájdený** | Údaj je formálne správny, ale v systéme nie je. | Asistent požiada o iný údaj. |
| **Nesprávny formát** | Údaj neprešiel validáciou (napr. rodné číslo s 5 ciframi). | Tool vráti zrozumiteľnú hlášku, čo má zákazník zadať. |
| **Technická chyba** | Systémy Telekomu neodpovedali. | Odporúčaná hláška: „Vyskytol sa technický problém. Prepojím vás na operátora." |

### Dôležité biznis pravidlá

- Úspešná identifikácia sa **pamätá 30 minút** v rámci konverzácie — nadväzujúce
  tooly (autentifikácia, viazanosť) z nej čerpajú a zákazník nemusí údaje opakovať.
- **Ochrana rodného čísla:** plné rodné číslo sa nikdy nevracia v odpovedi ani
  neposiela do konverzačnej vrstvy — von idú len posledné 4 cifry ako potvrdenie,
  že identifikácia prebehla.

---

## 2. `zobraz_identifikacny_widget` — identifikačný formulár (len chat)

Zobrazí zákazníkovi v chate **formulár na zadanie identifikačného údaja** —
jedno textové pole s vysvetlením, ktoré údaje sú akceptované.

**Logika:**

- Používa sa **len v chat kanáli**. V hlasovom kanáli sa formulár nezobrazuje —
  tool to sám odmietne a asistent si údaj vypýta slovne.
- Základný formulár má jedno voľné pole — typ údaja sa rozpozná automaticky.
- Ak predchádzajúca identifikácia skončila nejednoznačne („údaj sa dá chápať
  viacerými spôsobmi"), zobrazí sa **rozšírený variant s výberom typu údaja**
  (rozbaľovací zoznam: telefón / IČO / rodné číslo / kód zákazníka / sériové číslo).
- Po zobrazení formulára asistent **čaká** — nič ďalšie nerobí, kým zákazník
  formulár neodošle. Vyplnené hodnoty putujú bezpečným kanálom priamo do toolu
  `identifikacia` (mimo jazykového modelu).

**Deľba práce:** tento tool formulár **iba zobrazuje**; samotné spracovanie údaja
robí vždy `identifikacia`. To zaručuje, že zobrazenie a vyhodnotenie sa nikdy
nepomiešajú.

---

## 3. `autentifikacia` — overenie totožnosti

Po identifikácii overí, že volajúci/píšuci je **naozaj ten zákazník**. Funguje
ako postupný proces — volá sa opakovane, pri každom kroku vyhodnotí jeden
overovací faktor a povie, čo treba ďalej.

### Dve úrovne overenia

| Úroveň | Počet faktorov | Kedy sa vyžaduje (príklady) |
| --- | --- | --- |
| **Štandardná** | 2 faktory | bežné požiadavky — opätovné zaslanie faktúry, info o viazanosti |
| **Citlivá** | 3 faktory | zmena hesla, zmena fakturačných údajov |

Úroveň určuje konverzačná vrstva podľa toho, čo zákazník žiada. Ak nie je
určená, platí štandardná.

### Overovacie faktory — pevné poradie

Faktory sa vyhodnocujú **vždy v tomto poradí**:

| # | Faktor | Odkiaľ pochádza | Ako sa overuje |
| --- | --- | --- | --- |
| 1 | **Dôveryhodný zdroj** | automaticky — telefónne číslo/e-mail, z ktorého sa zákazník ozval | porovnanie s kontaktmi evidovanými pri zákazníkovi. Zákazník oň nie je nikdy žiadaný — buď sadne automaticky, alebo sa preskočí. |
| 2 | **Meno a priezvisko** | zákazník povie/napíše | tolerantné porovnanie — nezáleží na veľkosti písmen, diakritike ani poradí slov („Stano Muziková" = „MUZIKOVÁ STANO") |
| 3 | **Kód adresáta** | zákazník ho nájde na faktúre | presná zhoda s číslom fakturačného účtu zákazníka |
| 4 | **Posledné 4 cifry rodného čísla** | zákazník povie/napíše | zhoda s evidovaným rodným číslom |

### Automatické uznanie faktorov („kredit" z identifikácie)

Niektoré faktory sú splnené už samotnou identifikáciou — zákazník ich nemusí
dokladovať znova:

| Ako sa zákazník identifikoval | Automaticky uznaný faktor |
| --- | --- |
| rodným číslom | faktor 4 (znalosť rodného čísla už preukázal) |
| kódom fakturačného účtu (končí 1–9) | faktor 3 (kód adresáta = kód fakturačného účtu) |
| kódom zákazníka (končí 0), IČO, telefónom, sériovým číslom | žiadny |

Faktor 1 (dôveryhodný zdroj) sa prehodnocuje pri každom volaní — ak sa zhoduje
telefón/e-mail, z ktorého zákazník komunikuje, s evidovaným kontaktom, uzná sa
automaticky. Príklad: zákazník volá z čísla, ktoré máme v evidencii, a identifikoval
sa rodným číslom → má okamžite 2 faktory = **štandardné overenie bez jedinej otázky**.

### Pravidlá procesu

- **Jeden faktor na jedno volanie** — tool nikdy neprijme dva údaje naraz.
- **Maximálne 3 pokusy na faktor.** Po treťom nesprávnom pokuse sa faktor označí
  ako neúspešný a prechádza sa na ďalší v poradí.
- **Preskočenie faktora:** ak zákazník údaj nemá (napr. nemá poruke faktúru),
  faktor sa preskočí a pýta sa ďalší.
- **Nedá sa overiť:** ak už nezostáva dosť faktorov na dosiahnutie potrebnej
  úrovne, tool vráti pokyn **prepojiť zákazníka na operátora**.
- Výsledok overenia sa pamätá v rámci konverzácie — úspešne overený zákazník
  sa pri ďalšej požiadavke v tej istej konverzácii neoveruje znova.

### Možné výsledky

| Výsledok | Čo znamená |
| --- | --- |
| **Overený** | Dosiahnutý potrebný počet faktorov. Vracia aj úroveň (štandardná/citlivá). |
| **Potrebný ďalší faktor** | Tool vráti, ktorý faktor nasleduje, a navrhne znenie otázky pre zákazníka. |
| **Nesprávny údaj** | Údaj sa nezhoduje; tool oznámi počet zostávajúcich pokusov. |
| **Chýba identifikácia** | Zákazník ešte nebol identifikovaný — treba najprv `identifikacia`. |
| **Nejednoznačná identifikácia** | Identifikácia vrátila viac zhôd — overenie nie je možné, treba presnejší údaj. |
| **Nedá sa overiť** | Vyčerpané možnosti — prepojenie na operátora. |

---

## 4. `zobraz_autentifikacny_widget` — overovací formulár (len chat)

Zobrazí v chate **formulár pre práve pýtaný overovací faktor** (meno a priezvisko /
kód adresáta / posledné 4 cifry rodného čísla).

**Logika:**

- Faktor sa **určí automaticky** podľa stavu overenia — asistent nemusí vedieť,
  ktorý je na rade (voliteľne ho môže určiť).
- Formulár obsahuje nápovedu („Kód adresáta nájdete na svojej faktúre.") a tlačidlo
  **„Nemám / Neviem nájsť"** — zákazník ním faktor preskočí.
- Rovnako ako identifikačný widget: **len chat kanál**, po zobrazení asistent čaká
  na odoslanie a vyplnená hodnota ide bezpečným kanálom priamo do `autentifikacia`
  — nikdy nie cez jazykový model.
- Vyžaduje predchádzajúcu identifikáciu — bez nej nemá čo overovať a tool to oznámi.

---

## 5. `over_viazanost` — stav viazanosti zákazníka

Odpovedá na otázku **„Dokedy mám viazanosť?"**. Prvý „biznis" tool nadväzujúci
na identifikáciu a autentifikáciu.

### Podmienky

- Zákazník musí byť **identifikovaný** (jednoznačne) a **štandardne autentifikovaný**
  (2 faktory). Bez toho tool dáta nevydá a vráti pokyn, čo treba doplniť.

### Logika

1. Načíta **všetky produkty/služby zákazníka** z inventára produktov (podľa
   zákazníckeho čísla; pri identifikácii telefónom podľa telefónneho čísla).
2. Z každého produktu vyberie **zmluvy (viazanosti), ktoré sú dnes aktívne** —
   t. j. zmluva beží (dnešok je medzi začiatkom a koncom) a nie je ukončená,
   vypovedaná ani expirovaná.
3. Podľa **najneskoršieho dátumu konca viazanosti** zaradí zákazníka do kategórie:

| Kategória | Význam | Biznis kontext |
| --- | --- | --- |
| `Nema_viazanost` | žiadna aktívna viazanosť | voľný zákazník |
| `Prolongacne_okno` | viazanosť končí **do 90 dní** | priestor na retenčnú ponuku / predĺženie |
| `Viazanost_do_roka` | koniec o 90 – 365 dní | — |
| `Viazanost_viac_ako_rok` | koniec o viac než rok | — |

4. Vráti **zoznam služieb so zmluvami zoskupený podľa kategórií** (názov služby,
   identifikátor, dátum konca viazanosti) a **hotovú odporúčanú odpoveď** pre
   zákazníka — pri jednej službe jednou vetou („Viazanosť na Vami zadanom čísle
   je do: 15. 03. 2027."), pri viacerých službách prehľadný rozpis po riadkoch.

---

## 6. `nastav_test_kontext` — len na testovanie

**Nie je určený pre produkčnú prevádzku.** Umožňuje testerom simulovať údaje,
ktoré v produkcii prichádzajú automaticky z telefónnej ústredne / konverzačnej
platformy:

- **telefónne číslo/e-mail volajúceho** (podklad pre faktor 1 — dôveryhodný zdroj),
- **typ transakcie** (štandardná / citlivá — určuje počet faktorov),
- **kanál konverzácie** (chat / hlas — zapína alebo vypína widgety).

V produkčnom nasadení tieto hodnoty plne dodáva konverzačná platforma a tento
tool má byť odstránený alebo obmedzený.

---

## 7. Znalostná báza — `znalostna_baza_*` (voliteľné)

Štvorica toolov nad internou znalostnou bázou (dokumenty, návody, FAQ). Registrujú
sa **len ak je znalostná báza pre nasadenie nakonfigurovaná** — inak služba beží
čisto identitne. Sú nezávislé od identifikácie — nevyžadujú prihláseného zákazníka.

| Tool | Čo robí |
| --- | --- |
| `znalostna_baza_vyhladaj` | **Vyhľadá odpoveď na otázku** v prirodzenom jazyku. Prehľadá všetky nakonfigurované indexy naraz a vráti relevantné dokumenty s úryvkami textu. Podporuje filtrovanie podľa štítkov a kombinované (sémantické + kľúčové slová) vyhľadávanie. |
| `znalostna_baza_zoznam_dokumentov` | **Katalóg dokumentov** — stránkovaný zoznam s názvami, anotáciami a štítkami. Dá sa filtrovať podľa názvu a štítkov. |
| `znalostna_baza_detail_dokumentu` | **Celý jeden dokument** — kompletné metadáta a plný obsah po častiach. Používa sa, keď úryvok z vyhľadávania nestačí. |
| `znalostna_baza_stitky` | **Zoznam všetkých štítkov** (kategórií) v báze — asistent nimi zisťuje, aké filtre má k dispozícii. |

---

## Bezpečnosť a ochrana údajov — zhrnutie

| Pravidlo | Detail |
| --- | --- |
| **Rodné číslo sa nikdy nevracia celé** | V odpovediach ani smerom do konverzačnej vrstvy — len posledné 4 cifry. |
| **Údaje z formulárov obchádzajú jazykový model** | Vyplnené hodnoty idú priamo do toolov; LLM vidí len to, že formulár bol odoslaný. |
| **Zosnulí a zrušení zákazníci sa nevyhľadávajú** | Takéto záznamy identifikácia automaticky vylúči. |
| **Citlivé dáta až po overení** | Viazanosť (a budúce biznis tooly) vyžadujú identifikáciu + autentifikáciu. |
| **Limit pokusov** | Max. 3 pokusy na overovací faktor; po vyčerpaní možností prepojenie na operátora. |
| **Časové obmedzenie** | Identifikácia a overenie platia 30 minút v rámci jednej konverzácie. |
| **Iba čítanie** | Služba v systémoch Telekomu nič nemení. |

---

## Technická príloha — na čo slúži ktorý súbor

Prehľad všetkých súborov v priečinku `svc/mcp_telekom_identity/`. Kód je rozdelený
podľa zodpovedností: **tooly** (rozhranie pre AI asistenta), **doménová logika**
(čisté funkcie bez volaní von), **klienti** (komunikácia s externými systémami)
a **stav** (pamäť konverzácie).

### Tooly — rozhranie pre AI asistenta

| Súbor | Na čo slúži |
| --- | --- |
| [`tools.py`](tools.py) | **Hlavný súbor toolov.** Registruje všetky zákaznícke tooly (`identifikacia`, `autentifikacia`, oba widget tooly, `over_viazanost`, `nastav_test_kontext`) a obsahuje ich orchestráciu: dispatcher identifikácie (validácia vstupu → volanie správneho vyhľadania → zostavenie odpovede), stavový priebeh autentifikácie krok za krokom a tok overenia viazanosti. Samotné rozhodovacie pravidlá si požičiava z doménových modulov nižšie. |
| [`knowledge_base_tools.py`](knowledge_base_tools.py) | Registruje 4 tooly znalostnej bázy (`znalostna_baza_*`). Je to fasáda nad službou *indexer* — vyhľadávanie, katalóg dokumentov, detail dokumentu, štítky. Pri viacerých indexoch sa dopyty rozposielajú paralelne a výsledky zlučujú. Registruje sa len ak je znalostná báza nakonfigurovaná. |
| [`widgets.py`](widgets.py) | **Vzhľad formulárov (widgetov)** pre chat kanál v dizajne Telekomu (magenta, zaoblené polia). Definuje identifikačný formulár (základný aj variant s výberom typu údaja) a overovací formulár pre jednotlivé faktory vrátane tlačidla „Nemám / Neviem nájsť". Obsahuje aj „zmluvu" medzi formulárom a toolmi — názvy polí, pod ktorými sa vyplnené hodnoty odovzdávajú. |

### Doménová logika — čisté rozhodovacie pravidlá (bez volaní von)

| Súbor | Na čo slúži |
| --- | --- |
| [`classify.py`](classify.py) | **Rozpoznávanie typu identifikátora.** Normalizácia telefónnych čísel (`0902…` → `421902…`) a sériových čísel, kontrolné súčty IČO a rodného čísla a klasifikátor, ktorý zo zadaného textu určí typ údaja (alebo ohlási kolíziu, keď sa údaj dá chápať viacerými spôsobmi). |
| [`auth.py`](auth.py) | **Pravidlá autentifikácie.** Pevné poradie faktorov, počty faktorov pre štandardnú/citlivú úroveň, overovacie porovnania (tolerantné meno bez diakritiky, presný kód adresáta, posledné 4 cifry RČ, zhoda dôveryhodného zdroja s kontaktmi), automatické uznanie faktorov z identifikácie a texty otázok pre zákazníka. |
| [`viazanost.py`](viazanost.py) | **Pravidlá viazanosti.** Ktoré stavy zmlúv sa počítajú ako aktívne, filtrovanie zmlúv platných k dnešku, zaradenie do kategórií (bez viazanosti / prolongačné okno / do roka / viac ako rok) a zostavenie odporúčanej odpovede pre zákazníka. |
| [`candidates.py`](candidates.py) | **Prevod dát zo systémov Telekomu na jednotný tvar „kandidáta".** Zo surových záznamov (osoba/firma, zákazník) vyskladá jednotnú štruktúru: meno, kontakty, čísla fakturačných účtov, segment. Rieši aj špecialitu B2C mien uložených ako `Priezvisko,Meno` (otočí ich) a prekladá technické chyby DPS na zrozumiteľné chybové odpovede. Rodné číslo sa tu zámerne nikdy neprenáša celé. |

### Klienti — komunikácia s externými systémami

| Súbor | Na čo slúži |
| --- | --- |
| [`dps_get_client.py`](dps_get_client.py) | **HTTP klient na systémy Telekomu (DPS).** Výhradne čítacie volania na Party Management (osoby/firmy), Customer Management (zákazníci, fakturačné účty) a Product Inventory (služby, zariadenia, zmluvy). Rieši autorizáciu tokenom, časové limity a rozlišovanie druhov chýb (neplatný token / výpadok / timeout). |
| [`indexer_client.py`](indexer_client.py) | HTTP klient na službu *indexer* (znalostná báza) + krátkodobá cache zoznamu štítkov. Používajú ho len tooly znalostnej bázy. |
| [`nlp_state.py`](nlp_state.py) | **Synchronizácia s konverzačnou platformou (NLP engine).** Pred každým toolom si stiahne aktuálny stav konverzácie (kanál, caller-ID, hodnoty odoslané z formulárov) a po ňom odošle späť to, čo tooly zapísali (napr. „identifikácia prebehla"). Obsahuje dve kľúčové poistky: citlivé hodnoty z formulárov sa **nikdy neposielajú späť** a každá hodnota z formulára sa **spracuje len raz** (aby sa ten istý údaj nevyhodnocoval opakovane). Odosielanie je „fire-and-forget" — pomalá platforma nikdy nespomalí odpoveď zákazníkovi. |

### Stav a spustenie služby

| Súbor | Na čo slúži |
| --- | --- |
| [`_state.py`](_state.py) | **Pamäť konverzácie.** Všetky dočasné úložiská na jednom mieste: výsledok identifikácie, priebeh autentifikácie a zrkadlo stavu z konverzačnej platformy. Všetko s platnosťou 30 minút na konverzáciu; po vypršaní sa zákazník identifikuje znova. |
| [`__init__.py`](__init__.py) | **Definícia a konfigurácia služby.** Číta nastavenia z prostredia (adresa DPS, token, limity, voliteľná znalostná báza), vytvorí klientov a pri štarte zaregistruje tooly — identitné vždy, znalostnú bázu len ak je nakonfigurovaná. |
| [`__main__.py`](__main__.py) | Vstupný bod na spustenie: `python -m svc.mcp_telekom_identity`. |
| [`requirements.in`](requirements.in) | Zoznam Python knižníc, ktoré služba potrebuje. |
| [`README.md`](README.md) | Technická dokumentácia pre vývojárov — spustenie, testovacie dáta, detaily odpovedí. |
| `DOKUMENTACIA_BIZNIS.md` | Tento dokument. |

### Ako do seba súbory zapadajú (príklad: identifikácia telefónom)

```text
zákazník zadá „0902 804 660"
   → tools.py        prijme volanie toolu identifikacia
   → nlp_state.py    načíta stav konverzácie (kanál, hodnoty z formulára)
   → classify.py     rozpozná: je to telefónne číslo → 421902804660
   → dps_get_client.py  nájde službu s týmto číslom a jej zákazníka v DPS
   → candidates.py   prevedie záznam na kandidáta (meno, kontakty, účty)
   → _state.py       uloží výsledok na 30 minút pre ďalšie tooly
   → nlp_state.py    ohlási konverzačnej platforme, že identifikácia prebehla
   → tools.py        vráti asistentovi: {"found": true, "name": "Stano Muziková"}
```
