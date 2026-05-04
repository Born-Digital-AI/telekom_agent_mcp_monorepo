"""Disambiguation scripts for Telekom intent recognition.

Each entry:
  area         : unique ID matching DISAMBIGUATION_AREAS in intent_catalog.py
  question     : exact question to ask the customer (Slovak)
  instruction  : guidance for the LLM — when/how to use this question
  options      : list of possible customer answers with routing hints
  follow_up    : optional nested disambiguation if a specific option is chosen
"""

from __future__ import annotations

from typing import Any

CLARIFICATION_SCRIPTS: dict[str, dict[str, Any]] = {
    "tech_vs_nontech": {
        "area": "tech_vs_nontech",
        "question": "Nefunguje vám niektorá služba, alebo chcete niečo zmeniť či sa opýtať?",
        "instruction": (
            "Použi keď zákazník povedal 'nefunguje mi', 'mám problém', 'nejde mi' "
            "bez upresnenia čoho. Cieľ: rozlíšiť skutočnú technickú poruchu od "
            "obchodnej požiadavky alebo otázky."
        ),
        "options": [
            {
                "answer_pattern": "nefunguje|nejde|porucha|výpadok|technický problém",
                "label": "Technická porucha",
                "intent_hint": "Prejdi na 'which_service' — zistiť, ktorá služba nefunguje.",
                "next_disambiguation": "which_service",
            },
            {
                "answer_pattern": "zmeniť|zrušiť|aktivovať|informácia|faktúra|paušál|zmluva",
                "label": "Obchodná požiadavka / otázka",
                "intent_hint": (
                    "Zákazník nechce riešiť poruchu. Podľa témy zvoľ "
                    "selfcare alebo standard_contact_center intent."
                ),
                "next_disambiguation": None,
            },
        ],
    },
    "which_service": {
        "area": "which_service",
        "question": "Ktorá služba vám nefunguje?",
        "instruction": (
            "Použi keď vieš, že zákazník má technický problém, ale nevieš ktorú službu. "
            "Netlač, ak to zákazník už povedal. Ak zákazník spomína router, použi 'router_type'."
        ),
        "options": [
            {
                "answer_pattern": "internet|optika|dsl|gpon|kábel|broadband",
                "label": "Internet (pevný)",
                "intent_hint": (
                    "Pokračuj s 'internet_fault_type' — zistiť, či je úplne nedostupný alebo len pomalý. "
                    "Ak zákazník spomína wifi, použi aj 'wifi_vs_internet'."
                ),
                "next_disambiguation": "internet_fault_type",
            },
            {
                "answer_pattern": "televízia|tv|telka|magio|settopbox|iptv|satelit|sat",
                "label": "Televízia",
                "intent_hint": "Pravdepodobný intent: TV_FAILURE (route: tech_selfcare).",
                "next_disambiguation": "voyo_vs_tv",
            },
            {
                "answer_pattern": "mobilné dáta|dáta v mobile|internet v mobile|mobilný internet",
                "label": "Mobilné dáta",
                "intent_hint": "Pravdepodobný intent: MOBILE_INTERNET (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "volania|mobilná linka|gsm|sim|hovory|telefonovanie mobilné",
                "label": "Mobilné volania / linka",
                "intent_hint": "Pravdepodobný intent: MOBILE_ISSUES (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "pevná linka|aparát|prístroj|telefón pevný|landline",
                "label": "Pevná linka",
                "intent_hint": "Pravdepodobný intent: PSTN_FAILURE (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "wifi|wi-fi|bezdrôt",
                "label": "WiFi",
                "intent_hint": "Použi 'wifi_vs_internet' — zistiť, či je to HW router alebo len WiFi signál.",
                "next_disambiguation": "wifi_vs_internet",
            },
            {
                "answer_pattern": "router|ruter|modem",
                "label": "Router",
                "intent_hint": "Použi 'router_type' — zistiť, či je router pokazený alebo ide o výmenu.",
                "next_disambiguation": "router_type",
            },
            {
                "answer_pattern": "všetko|viaceré služby|aj internet aj tv|všetky",
                "label": "Viacero služieb naraz",
                "intent_hint": "Pravdepodobný intent: OUTAGE (route: tech_selfcare) — výpadok viacerých služieb.",
                "next_disambiguation": None,
            },
        ],
    },
    "router_type": {
        "area": "router_type",
        "question": "Dostali ste SMS alebo správu o bezplatnej výmene routra, alebo vám router sám prestal fungovať?",
        "instruction": (
            "Kľúčová otázka na rozlíšenie HW_WIFI_ROUTER (tech_selfcare) "
            "od ROUTER_REPLACEMENT_CALLBACK (standard_contact_center). "
            "Zákazník môže povedať 'router' pri oboch situáciách."
        ),
        "options": [
            {
                "answer_pattern": "dostal som sms|správa o výmene|notifikácia|bezplatná výmena|email o routri|písali mi",
                "label": "Reaguje na notifikáciu o výmene",
                "intent_hint": "Intent: ROUTER_REPLACEMENT_CALLBACK (route: standard_contact_center).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "nefunguje|pokazený|prestal fungovať|sám sa pokazil|bliká|nezapína",
                "label": "Router je pokazený / nefunguje",
                "intent_hint": "Intent: HW_WIFI_ROUTER (route: tech_selfcare).",
                "next_disambiguation": None,
            },
        ],
    },
    "internet_fault_type": {
        "area": "internet_fault_type",
        "question": "Je internet úplne nedostupný, alebo je len pomalý, prípadne občas vypadáva?",
        "instruction": (
            "Použi keď zákazník hovorí o probléme s pevným internetom, "
            "ale nie je jasné, či je úplne nedostupný alebo len nestabilný."
        ),
        "options": [
            {
                "answer_pattern": "úplne nejde|vôbec nejde|nič|nedostupný|neviem sa pripojiť|žiadne pripojenie",
                "label": "Úplne nedostupný",
                "intent_hint": "Intent: INTERNET_NO_SERVICE (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "pomalý|ide pomaly|nestabilný|vypadáva|občas|prerušovaný|slabý signál",
                "label": "Pomalý alebo nestabilný",
                "intent_hint": "Intent: INTERNET_SLOW (route: tech_selfcare).",
                "next_disambiguation": None,
            },
        ],
    },
    "wifi_vs_internet": {
        "area": "wifi_vs_internet",
        "question": "Máte problém len s WiFi signálom (napr. slabý dosah v niektorej izbe), alebo internet nefunguje vôbec — ani cez kábel?",
        "instruction": (
            "Použi keď zákazník spomína wifi alebo router pri probléme s internetom. "
            "Rozlišuje SW_WIFI (route: tech_selfcare) od INTERNET_NO_SERVICE / INTERNET_SLOW. "
            "Ak zákazník zmieňuje router ako HW, môže ísť aj o HW_WIFI_ROUTER."
        ),
        "options": [
            {
                "answer_pattern": "len wifi|wifi signál|v izbe|dosah|slabý wifi|wifi heslo|nastaviť wifi",
                "label": "Len WiFi signál / nastavenie",
                "intent_hint": "Intent: SW_WIFI (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "internet vôbec|ani kábel|cez kábel tiež nefunguje|úplne nedostupný",
                "label": "Internet nefunguje vôbec",
                "intent_hint": "Pokračuj s 'internet_fault_type' pre spresnenie.",
                "next_disambiguation": "internet_fault_type",
            },
            {
                "answer_pattern": "router nefunguje|router je pokazený|bliká router|nezapína sa",
                "label": "Router samotný nefunguje",
                "intent_hint": "Intent: HW_WIFI_ROUTER (route: tech_selfcare). Zvážiť aj 'router_type'.",
                "next_disambiguation": "router_type",
            },
        ],
    },
    "mobile_issue_type": {
        "area": "mobile_issue_type",
        "question": "Máte problém s volaniami, alebo s mobilnými dátami (internetom v mobile)?",
        "instruction": (
            "Použi keď zákazník hovorí o probléme s mobilom/telefónom, "
            "ale nie je jasné, či ide o volania alebo mobilné dáta."
        ),
        "options": [
            {
                "answer_pattern": "volania|hovory|telefonovanie|neviem zavolať|hovor neprechádza|volám a nevie|linka",
                "label": "Mobilné volania",
                "intent_hint": "Intent: MOBILE_ISSUES (route: tech_selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "dáta|internet v mobile|mobilný internet|nefungujú dáta|prenášanie dát",
                "label": "Mobilné dáta",
                "intent_hint": "Intent: MOBILE_INTERNET (route: tech_selfcare).",
                "next_disambiguation": None,
            },
        ],
    },
    "service_change_vs_fault": {
        "area": "service_change_vs_fault",
        "question": "Chcete zmeniť alebo aktivovať službu, alebo vám niečo nefunguje?",
        "instruction": (
            "Použi keď nie je jasné, či zákazník hlási technický problém alebo chce zmenu/aktiváciu. "
            "Typicky pri slovách 'internet', 'program', 'paušál' bez kontextu."
        ),
        "options": [
            {
                "answer_pattern": "zmeniť|aktivovať|zrušiť|pridať|upgradeovať|nový balík|lepší paušál",
                "label": "Zmena / aktivácia služby",
                "intent_hint": (
                    "Obchodná požiadavka — route: standard_contact_center. "
                    "Upresni konkrétny intent (SERVICE_ADMINISTRATION, AKTIVACIA, SERVICE_TERMINATION...)."
                ),
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "nefunguje|nejde|výpadok|porucha|problém technický",
                "label": "Technický problém",
                "intent_hint": "Technická porucha — prejdi na 'which_service'.",
                "next_disambiguation": "which_service",
            },
        ],
    },
    "voyo_vs_tv": {
        "area": "voyo_vs_tv",
        "question": "Ide o aplikáciu VOYO, alebo o televíziu (Magio, settopbox)?",
        "instruction": (
            "Použi keď zákazník spomína TV/televíziu bez upresnenia, "
            "alebo keď môže ísť o VOYO (streaming app) alebo klasickú TV (Magio IPTV, SAT)."
        ),
        "options": [
            {
                "answer_pattern": "voyo|vojo|streaming|aplikácia voyo",
                "label": "VOYO aplikácia",
                "intent_hint": "Intent: VOYO (route: selfcare).",
                "next_disambiguation": None,
            },
            {
                "answer_pattern": "televízia|tv|telka|magio|settopbox|box|satelit|signál",
                "label": "Televízia (Magio, settopbox)",
                "intent_hint": "Intent: TV_FAILURE (route: tech_selfcare) pre technické problémy, alebo TV_PACKAGES pre obchodné.",
                "next_disambiguation": None,
            },
        ],
    },
}
