"""WiFi troubleshooting steps and reference data.
Based on: https://www.telekom.sk/wiki/internet/nefunguje-mi-wi-fi

Three problem types with different step sequences:
  wifi_not_working — WiFi vôbec nefunguje
  slow_wifi        — Pomalý internet cez WiFi
  weak_signal      — Slabý dosah WiFi
"""

from __future__ import annotations

from typing import Any

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Diagnostic options (Phase 1)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DIAGNOSTIC_OPTIONS = [
    {
        "id": "wifi_not_working",
        "label": "WiFi vôbec nefunguje",
        "trigger_phrases": [
            "nejde wifi",
            "wifi nefunguje",
            "nepripojí sa",
            "nevidím wifi sieť",
            "žiadna wifi",
            "nefunguje",
        ],
    },
    {
        "id": "slow_wifi",
        "label": "Pomalý internet cez WiFi",
        "trigger_phrases": [
            "pomalý",
            "pomaly",
            "dlho trvá",
            "laguje",
            "pomalé",
            "rýchlosť",
            "buffering",
            "zasekáva",
        ],
    },
    {
        "id": "weak_signal",
        "label": "Slabý dosah WiFi",
        "trigger_phrases": [
            "slabý signál",
            "dosah",
            "v izbe nechytá",
            "ďaleko od routera",
            "nechytám",
            "signál",
            "len pri routeri",
            "v kuchyni nejde",
        ],
    },
]

DIAGNOSTIC_QUESTION = "Aký problém má zákazník s WiFi?"
DIAGNOSTIC_INSTRUCTION = (
    "Opýtajte sa zákazníka, aký presne problém má. "
    "Zavolajte tool znova s jeho odpoveďou v step_result."
)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step sequences per problem type
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEPS_BY_PROBLEM: dict[str, list[str]] = {
    "wifi_not_working": [
        "restart_router",
        "check_wlan_led",
        "check_wifi_password",
        "check_service_mode",
        "channel_change",
    ],
    "slow_wifi": [
        "restart_router",
        "check_placement",
        "channel_change",
        "lan_speed_test",
    ],
    "weak_signal": [
        "check_placement",
        "check_interference",
        "wow_wifi_info",
    ],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reference images
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_STORYBLOK = "https://a.storyblok.com/f/285923"
_WIKI = "https://www.telekom.sk/wiki/internet/nefunguje-mi-wi-fi"
_YT_PLACEMENT = "https://www.youtube.com/watch?v=i7oONziP0HQ"
_YT_PLACEMENT_THUMB = "https://img.youtube.com/vi/i7oONziP0HQ/mqdefault.jpg"

STEP_REFERENCE_IMAGES: dict[str, list[dict[str, str]]] = {
    "restart_router": [
        {
            "description": "Nálepka na routeri s prihlasovacími údajmi",
            "url": f"{_STORYBLOK}/481x271/196e84579e/stitok.png",
        },
    ],
    "check_wlan_led": [],  # router-specific LED images are in ROUTER_REFERENCE_IMAGES
    "check_wifi_password": [
        {
            "description": "Nálepka routera — WLAN key (heslo pre WiFi)",
            "url": f"{_STORYBLOK}/481x271/196e84579e/stitok.png",
        },
        {
            "description": "Nálepka Sercomm zariadenia",
            "url": f"{_STORYBLOK}/694x383/96e35023f2/sercom_stitok.png",
        },
    ],
    "check_service_mode": [],
    "channel_change": [],
    "check_placement": [],
    "check_interference": [],
    "lan_speed_test": [],
    "wow_wifi_info": [],
}

ROUTER_REFERENCE_IMAGES: dict[str, list[dict[str, str]]] = {
    "Sagemcom 5670 AX": [
        {
            "description": "Sagemcom 5670 AX — LED indikátory",
            "url": f"{_STORYBLOK}/498x759/90571cae69/sagemcom-5670-ax.png",
        },
    ],
    "Sagemcom 5655 AC": [
        {
            "description": "Sagemcom 5655 AC — LED indikátory",
            "url": f"{_STORYBLOK}/826x863/5bf060daf6/sagemcom_5655_ac.jpg",
        },
        {
            "description": "Sagemcom 5655 AC — prihlásenie",
            "url": f"{_STORYBLOK}/598x431/5e42e4cecc/sagemcom-5655-ac-login.png",
        },
        {
            "description": "Sagemcom 5655 AC — WiFi nastavenia",
            "url": f"{_STORYBLOK}/954x605/07df09334f/sagemcom-5655-ac-settings.png",
        },
        {
            "description": "Sagemcom 5655 AC — zmena kanála",
            "url": f"{_STORYBLOK}/971x765/d773f01218/sagemcom-5655-ac-channel.png",
        },
    ],
    "Vantiva FGA2235": [
        {
            "description": "Vantiva FGA2235 — LED indikátory",
            "url": f"{_STORYBLOK}/605x488/b0ac3b1ad9/vantiva-fga2235.png",
        },
        {
            "description": "Vantiva FGA2235 — prihlásenie",
            "url": f"{_STORYBLOK}/605x280/23016c2bae/vantiva_1_login.png",
        },
        {
            "description": "Vantiva FGA2235 — Wireless nastavenia",
            "url": f"{_STORYBLOK}/605x498/f5d37bc1ae/vantiva_2.png",
        },
        {
            "description": "Vantiva FGA2235 — výber kanála",
            "url": f"{_STORYBLOK}/605x628/9473180699/vantiva_3.png",
        },
    ],
    "ZTE ENTRY II": [
        {
            "description": "ZTE ENTRY II — LED indikátory",
            "url": f"{_STORYBLOK}/886x650/00c603e6c8/zte_entry_ii.jpg",
        },
        {
            "description": "ZTE ENTRY II — prihlásenie",
            "url": f"{_STORYBLOK}/1202x385/bf9e55a50f/zte-entry-ii-login.png",
        },
        {
            "description": "ZTE ENTRY II — WLAN nastavenia",
            "url": f"{_STORYBLOK}/949x544/2d01601563/zte_entry_ii_nastavenie_wlan.png",
        },
        {
            "description": "ZTE ENTRY II — konfigurácia kanála",
            "url": f"{_STORYBLOK}/969x1035/c50d00f3c6/zte-entry-ii-config.png",
        },
    ],
    "ADB VV3212": [
        {
            "description": "ADB VV3212 — LED indikátory",
            "url": f"{_STORYBLOK}/862x517/4bef18829c/adb_vv3212.jpg",
        },
        {
            "description": "ADB VV3212 — prihlásenie",
            "url": f"{_STORYBLOK}/460x237/ecc4d30ff5/adb-vv3212-login.png",
        },
        {
            "description": "ADB VV3212 — konfigurácia",
            "url": f"{_STORYBLOK}/1216x664/1cf947bfea/adb_vv3212_konfiguracia.png",
        },
        {
            "description": "ADB VV3212 — susedské siete",
            "url": f"{_STORYBLOK}/1013x521/f31173817b/adb-vv3212-channel.png",
        },
        {
            "description": "ADB VV3212 — zmena kanála",
            "url": f"{_STORYBLOK}/988x522/7815cb7354/adb-vv3212-settings.png",
        },
    ],
    "Sercomm Speedport+": [
        {
            "description": "Sercomm Speedport+ — LED indikátory",
            "url": f"{_STORYBLOK}/883x648/b6fbaae8ed/sercomm_speedport.jpg",
        },
        {
            "description": "Sercomm Speedport+ — prihlásenie",
            "url": f"{_STORYBLOK}/964x555/51f1064c5d/sercomm-login.png",
        },
        {
            "description": "Sercomm Speedport+ — nastavenia kanála",
            "url": f"{_STORYBLOK}/964x871/22de9249d5/sercomm-settings.png",
        },
    ],
    "Sercomm Speedport Plus 2": [
        {
            "description": "Sercomm SP2 — LED indikátory",
            "url": f"{_STORYBLOK}/605x425/f98ef6b7cf/sercomm-sp2.png",
        },
        {
            "description": "Sercomm SP2 — prihlásenie",
            "url": f"{_STORYBLOK}/605x267/ac2019ec1d/sp2-1.png",
        },
        {
            "description": "Sercomm SP2 — WiFi nastavenia",
            "url": f"{_STORYBLOK}/605x280/50806b17e1/sp2-2.png",
        },
    ],
    "Huawei HG8245 H": [
        {
            "description": "Huawei HG8245 H — LED indikátory",
            "url": f"{_STORYBLOK}/1240x852/d09417a7cc/huawei_hg8245h.png",
        },
        {
            "description": "Huawei HG8245 H — prihlásenie",
            "url": f"{_STORYBLOK}/575x251/3ee6cd0c6d/huawei-login.png",
        },
        {
            "description": "Huawei HG8245 H — výber kanála",
            "url": f"{_STORYBLOK}/1076x788/41afe121db/huawei-channel.jpg",
        },
    ],
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Step definitions (call + chat variants)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP_DEFINITIONS: dict[str, dict[str, Any]] = {
    "restart_router": {
        "title": {"call": "Reštart routera", "chat": "Reštart routera"},
        "instruction": {
            "call": (
                "Odpojte router zo zásuvky. Počkajte aspoň 15 sekúnd "
                "a znova ho zapojte. Potom počkajte asi 2 minúty, kým sa úplne naštartuje."
            ),
            "chat": (
                "**Reštartujte router:**\n"
                "1. Odpojte router zo zásuvky\n"
                "2. Počkajte **minimálne 15 sekúnd**\n"
                "3. Znova zapojte a počkajte **2 minúty**\n\n"
                f"[![Optimálne umiestnenie routera]({_YT_PLACEMENT_THUMB})]({_YT_PLACEMENT})"
            ),
        },
        "expected_result": "WiFi funguje po reštarte.",
        "confirmation_prompt": "Pomohol reštart? Funguje teraz WiFi?",
    },
    "check_wlan_led": {
        "title": {"call": "Kontrola WLAN indikátora", "chat": "Kontrola WLAN indikátora"},
        "instruction": {
            "call": (
                "Pozrite sa na kontrolky na routeri. Svieti WLAN alebo WiFi kontrolka? "
                "Ak nesvieti, hľadajte tlačidlo WLAN alebo WiFi na boku alebo zadnej strane routera a stlačte ho."
            ),
            "chat": (
                "Skontrolujte **WLAN/WiFi indikátor** na routeri:\n"
                "- **Svieti** → WiFi je zapnuté ✓\n"
                "- **Nesvieti** → stlačte tlačidlo **WLAN/WiFi** na routeri\n\n"
                "Tlačidlo nájdete väčšinou na boku alebo zadnej strane."
            ),
        },
        "expected_result": "WLAN/WiFi kontrolka svieti.",
        "confirmation_prompt": "Svieti teraz WLAN kontrolka na routeri?",
    },
    "check_wifi_password": {
        "title": {"call": "Overenie WiFi hesla", "chat": "Overenie WiFi hesla"},
        "instruction": {
            "call": (
                "Uistite sa, že zadávate správne WiFi heslo. "
                "Na nálepke routera je údaj WLAN key — to je heslo pre WiFi sieť. "
                "Nie je to heslo na prihlásenie do nastavení routera, to je iný údaj."
            ),
            "chat": (
                "Overte, že používate správne heslo:\n"
                "- **WLAN key** (na nálepke routera) = heslo pre WiFi sieť\n"
                "- **Admin heslo** = len pre správu routera na 192.168.1.1\n\n"
                "Heslo WLAN key nájdete na **nálepke na spodku alebo boku routera**.\n\n"
                f"[![Nálepka routera s heslami]({_STORYBLOK}/481x271/196e84579e/stitok.png)]({_WIKI})"
            ),
        },
        "expected_result": "Zariadenie sa pripojí s WLAN key heslom.",
        "confirmation_prompt": "Podarilo sa pripojiť s heslom z nálepky?",
    },
    "check_service_mode": {
        "title": {"call": "Kontrola režimu routera", "chat": "Režim ROUTE vs BRIDGE"},
        "instruction": {
            "call": (
                "Otvorte na počítači prehliadač a zadajte adresu 192.168.1.1. "
                "Prihláste sa heslom z nálepky routera. "
                "Skontrolujte, či je router nastavený na mód ROUTE. "
                "Ak je v móde BRIDGE, WiFi nebude fungovať."
            ),
            "chat": (
                "Prihláste sa na [192.168.1.1](http://192.168.1.1) a skontrolujte:\n"
                "- **ROUTE mód** = správne, WiFi funguje ✓\n"
                "- **BRIDGE mód** = WiFi je vypnuté ✗\n\n"
                "Ak je nastavený BRIDGE, prepnite na **ROUTE** a uložte."
            ),
        },
        "expected_result": "Router je v móde ROUTE.",
        "confirmation_prompt": "Je router v móde ROUTE? Funguje WiFi?",
    },
    "channel_change": {
        "title": {"call": "Zmena WiFi kanála", "chat": "Zmena WiFi kanála"},
        "instruction": {
            "call": "ROUTER_SPECIFIC",
            "chat": "ROUTER_SPECIFIC",
        },
        "expected_result": "Pripojenie na novom WiFi kanáli funguje.",
        "confirmation_prompt": "Funguje WiFi po zmene kanála?",
    },
    "check_placement": {
        "title": {"call": "Umiestnenie routera", "chat": "Umiestnenie routera"},
        "instruction": {
            "call": (
                "Router by mal byť umiestnený na vyvýšenom mieste, ideálne v strede bytu. "
                "Nie za skriňou, nie v rohu, nie pri podlahe. "
                "Hrubé steny a kovové predmety signál zoslabujú."
            ),
            "chat": (
                "**Optimálne umiestnenie routera:**\n"
                "- Na vyvýšenom mieste (polička, skriňa)\n"
                "- Ideálne v **strede bytu/domu**\n"
                "- **Nie** za skriňou, v rohu, pri podlahe\n"
                "- Ďalej od kovových predmetov a mikrovlnky\n\n"
                f"[![Optimálne umiestnenie routera]({_YT_PLACEMENT_THUMB})]({_YT_PLACEMENT})"
            ),
        },
        "expected_result": "Signál WiFi je silnejší po premiestnení.",
        "confirmation_prompt": "Zlepšil sa signál po premiestnení routera?",
    },
    "check_interference": {
        "title": {"call": "Rušenie WiFi signálu", "chat": "Rušenie WiFi signálu"},
        "instruction": {
            "call": (
                "WiFi signál môžu rušiť iné zariadenia — mikrovlnka, baby monitor, "
                "bezdrôtové telefóny alebo susedské WiFi siete. "
                "Skúste tieto zariadenia vypnúť alebo oddialiť od routera."
            ),
            "chat": (
                "**Čo môže rušiť WiFi signál:**\n"
                "- Mikrovlnná rúra\n"
                "- Baby monitor, bezdrôtový telefón\n"
                "- Susedské WiFi siete (rovnaký kanál)\n"
                "- Hrubé betónové steny, kovy\n\n"
                "Skúste problémové zariadenia vypnúť alebo router od nich oddialiť."
            ),
        },
        "expected_result": "Signál sa zlepšil po odstránení rušenia.",
        "confirmation_prompt": "Zlepšil sa signál po odstránení rušivých zariadení?",
    },
    "lan_speed_test": {
        "title": {"call": "Test cez LAN kábel", "chat": "Test rýchlosti cez LAN kábel"},
        "instruction": {
            "call": (
                "Pripojte počítač priamo do routera cez LAN kábel a otestujte rýchlosť internetu. "
                "Ak je aj cez kábel pomalý, problém nie je vo WiFi ale v internetovej službe."
            ),
            "chat": (
                "**Test rýchlosti cez kábel:**\n"
                "1. Pripojte počítač do routera **LAN káblom**\n"
                "2. Otvorte [speedtest.net](https://www.speedtest.net)\n"
                "3. Spustite test\n\n"
                "- **Kábel rýchly, WiFi pomalé** → problém je vo WiFi (zmena kanála môže pomôcť)\n"
                "- **Aj kábel pomalý** → problém je v internetovej službe, nie vo WiFi"
            ),
        },
        "expected_result": "Rýchlosť cez kábel je v poriadku (problém je len vo WiFi).",
        "confirmation_prompt": "Aká je rýchlosť cez kábel? Je výrazne vyššia ako cez WiFi?",
    },
    "wow_wifi_info": {
        "title": {"call": "Služba Wow WiFi Premium", "chat": "Služba Wow WiFi Premium"},
        "instruction": {
            "call": (
                "Ak máte väčší byt alebo dom a signál jedného routera nestačí, "
                "Slovak Telekom ponúka službu Wow WiFi Premium. "
                "Je to mesh systém, ktorý pokryje aj väčšie priestory. "
                "Chcete, aby som vás prepojil na operátora pre viac informácií?"
            ),
            "chat": (
                "**Wow WiFi Premium** — mesh systém pre lepšie pokrytie:\n"
                "- Pokryje aj väčšie byty a domy\n"
                "- Automatické prepínanie medzi bodmi\n"
                "- Bez mŕtvych zón\n\n"
                "Pre aktiváciu alebo viac informácií kontaktujte operátora."
            ),
        },
        "expected_result": "Zákazník má záujem o Wow WiFi alebo informáciu o pokrytí.",
        "confirmation_prompt": "Máte záujem o službu Wow WiFi, alebo ste problém vyriešili inak?",
    },
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Router-specific channel change instructions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ROUTER_CHANNEL_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "Sagemcom 5670 AX": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa heslom z nálepky. "
            "Kliknite na ikonu ozubeného kolesa, potom WiFi 2.4 GHz a záložku Basic. "
            "Zmeňte kanál z Auto na číslo 1, 6 alebo 11 a uložte."
        ),
        "chat": (
            "**Zmena WiFi kanála — Sagemcom 5670 AX:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa heslom z nálepky routera\n"
            "3. Kliknite na ⚙️ (ozubené koleso) → **WiFi 2.4GHz** → záložka **Basic**\n"
            "4. Zmeňte **Channel** z *Auto* na **1**, **6** alebo **11**\n"
            "5. Kliknite **Uložiť**"
        ),
    },
    "Sagemcom 5655 AC": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa heslom z nálepky. "
            "Kliknite na ikonu ozubeného kolesa, potom WiFi 2.4 GHz a záložku Basic. "
            "Zmeňte kanál z Auto na číslo 1, 6 alebo 11 a uložte."
        ),
        "chat": (
            "**Zmena WiFi kanála — Sagemcom 5655 AC:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa heslom z nálepky routera\n"
            "3. Kliknite na ⚙️ → **WiFi 2.4GHz** → záložka **Basic**\n"
            "4. Zmeňte **Channel** z *Auto* na **1**, **6** alebo **11**\n"
            "5. Kliknite **Uložiť**"
        ),
    },
    "Vantiva FGA2235": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "V menu kliknite na Wireless, vyberte sieť a zmeňte kanál "
            "z Auto na číslo 1, 6 alebo 11."
        ),
        "chat": (
            "**Zmena WiFi kanála — Vantiva FGA2235:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. Menu → **Wireless** → vyberte sieť\n"
            "4. Zmeňte **Channel** na **1**, **6** alebo **11**\n"
            "5. Uložte nastavenia"
        ),
    },
    "ZTE ENTRY II": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "Kliknite na Nastavenie WLAN, potom Global Configuration. "
            "Zmeňte kanál a kliknite Apply."
        ),
        "chat": (
            "**Zmena WiFi kanála — ZTE ENTRY II:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. **Nastavenie WLAN** → **Global Configuration**\n"
            "4. Zmeňte **Channel** na **1**, **6** alebo **11**\n"
            "5. Kliknite **Apply**"
        ),
    },
    "ADB VV3212": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "Kliknite na ikonu ceruzky, potom Advanced Configuration. "
            "Zmeňte kanál a uložte."
        ),
        "chat": (
            "**Zmena WiFi kanála — ADB VV3212:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. Kliknite na ✏️ (ceruzka) → **Advanced Configuration**\n"
            "4. Zmeňte **Channel** na **1**, **6** alebo **11**\n"
            "5. Uložte nastavenia"
        ),
    },
    "Sercomm Speedport+": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "Kliknite na Domáca sieť, potom Základné nastavenia WLAN. "
            "Zmeňte kanál a kliknite Odoslať nastavenia."
        ),
        "chat": (
            "**Zmena WiFi kanála — Sercomm Speedport+:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. **Domáca sieť** → **Základné nastavenia WLAN**\n"
            "4. Zmeňte **Channel** na **1**, **6** alebo **11**\n"
            "5. Kliknite **Odoslať nastavenia**"
        ),
    },
    "Sercomm Speedport Plus 2": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "Kliknite na Wi-Fi, potom Všeobecné. "
            "Zmeňte kanál a kliknite Uložiť."
        ),
        "chat": (
            "**Zmena WiFi kanála — Sercomm Speedport Plus 2:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. **Wi-Fi** → **Všeobecné**\n"
            "4. Zmeňte **Channel** na **1**, **6** alebo **11**\n"
            "5. Kliknite **Uložiť**"
        ),
    },
    "Huawei HG8245 H": {
        "call": (
            "Otvorte prehliadač a zadajte 192.168.1.1. Prihláste sa. "
            "Nájdite nastavenia WiFi kanála a zmeňte ho na číslo 1, 6 alebo 11."
        ),
        "chat": (
            "**Zmena WiFi kanála — Huawei HG8245 H:**\n"
            "1. Otvorte [192.168.1.1](http://192.168.1.1)\n"
            "2. Prihláste sa\n"
            "3. Nájdite nastavenia **WiFi Channel**\n"
            "4. Zmeňte na **1**, **6** alebo **11**\n"
            "5. Uložte nastavenia"
        ),
    },
}


def match_problem_type(answer: str) -> str | None:
    """Match customer's answer to a problem type using trigger phrases."""
    answer_lower = answer.strip().lower()
    for opt in DIAGNOSTIC_OPTIONS:
        for phrase in opt["trigger_phrases"]:
            if phrase in answer_lower:
                return opt["id"]
    # Also accept direct ID
    for opt in DIAGNOSTIC_OPTIONS:
        if answer_lower == opt["id"]:
            return opt["id"]
    return None


def get_step(
    step_id: str,
    channel: str,
    router_model: str | None = None,
) -> dict[str, Any]:
    """Build a single troubleshooting step for the given channel and router."""
    defn = STEP_DEFINITIONS.get(step_id)
    if not defn:
        return {"error": f"Unknown step_id: {step_id}"}

    instruction = defn["instruction"].get(channel, defn["instruction"].get("call", ""))

    # Router-specific override for channel_change step
    if instruction == "ROUTER_SPECIFIC" and router_model:
        router_instr = ROUTER_CHANNEL_INSTRUCTIONS.get(router_model, {})
        instruction = router_instr.get(
            channel, "Zmena kanála pre tento model nie je dostupná. Kontaktujte technickú podporu."
        )

    ref_images = list(STEP_REFERENCE_IMAGES.get(step_id, []))
    if step_id == "channel_change" and router_model:
        ref_images.extend(ROUTER_REFERENCE_IMAGES.get(router_model, []))

    return {
        "step_id": step_id,
        "title": defn["title"].get(channel, defn["title"].get("call", "")),
        "instruction": instruction,
        "expected_result": defn["expected_result"],
        "requires_confirmation": True,
        "confirmation_prompt": defn["confirmation_prompt"],
        "step_result_options": {
            "resolved": defn["expected_result"],
            "not_resolved": "Krok nepomohol, problém pretrváva.",
            "skipped": "Zákazník krok preskočil alebo ho nemohol vykonať.",
        },
        "reference_images": ref_images,
    }
