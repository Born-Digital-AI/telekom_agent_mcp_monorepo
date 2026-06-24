"""Da-Bubble widget trees for the identity service (chat channel only).

These builders render deterministic Da-Bubble component trees in the Telekom
house style. The *transport* envelope and the privacy-safe submit action come
from the shared :mod:`lib.bubble_widgets` helpers; the trees themselves are
service-specific and live here.

Privacy contract: on submit the host writes the field values into the
conversation's ``named_entities`` (under the ``name`` of each input) and emits
only the hidden utterance. The identification/authentication tools then read
those values from ``named_entities`` — the raw input never enters the LLM turn.
The ``name`` of every input below therefore IS the ``named_entities`` key its
owning tool reads; keep them in lock-step with ``tools.py``.
"""

from __future__ import annotations

from typing import Any

from lib.bubble_widgets import hidden_submit_action

# --- Telekom house style ---------------------------------------------------
_MAGENTA = "#E20074"
_INK = "#0F172A"
_MUTED = "#64748B"
_WHITE = "#ffffff"

# --- named_entities keys + submit utterances (the cross-process contract) ---
IDENT_INPUT_KEY = "identifikacia_vstup"
IDENT_TYPE_KEY = "identifikacia_typ"
IDENT_SUBMIT_UTTERANCE = "identifikacia_widget_submitted"

AUTH_FIELD_KEYS = {
    "name": "autentifikacia_meno_priezvisko",
    "kod_adresata": "autentifikacia_kod_adresata",
    "rc_last4": "autentifikacia_rc_last4",
}
AUTH_SUBMIT_UTTERANCE = "autentifikacia_widget_submitted"
AUTH_SKIP_UTTERANCE = "autentifikacia_faktor_skip"

# Identifier types offered in the dropdown. "auto" (default) lets the classifier
# decide; the rest are explicit overrides matching tools.py route keys.
IDENT_TYPE_OPTIONS = (
    ("auto", "Automaticky rozpoznať"),
    ("telefon", "Telefónne číslo"),
    ("ico", "IČO"),
    ("rodne_cislo", "Rodné číslo"),
    ("kod_zakaznika", "Zákaznícke / fakturačné číslo"),
    ("seriove_cislo", "Sériové číslo zariadenia"),
)


def _field_style() -> dict[str, Any]:
    return {
        "border": f"2px solid {_MAGENTA}",
        "borderRadius": "15px",
        "outline": "none",
        "boxShadow": "none",
    }


def _primary_button(label: str, utterance: str) -> dict[str, Any]:
    return {
        "type": "Button",
        "label": label,
        "submit": True,
        "block": True,
        "variant": "solid",
        "stylePreset": "primary",
        "size": "md",
        "style": {"backgroundColor": _MAGENTA, "color": _WHITE, "borderColor": _MAGENTA},
        "onClickAction": hidden_submit_action(utterance),
    }


def _ghost_button(label: str, utterance: str) -> dict[str, Any]:
    return {
        "type": "Button",
        "label": label,
        "submit": False,
        "block": True,
        "variant": "outline",
        "size": "md",
        "style": {"color": _MUTED, "borderColor": "#E2E8F0", "borderRadius": "15px"},
        "onClickAction": hidden_submit_action(utterance),
    }


def _select(name: str, options: tuple[tuple[str, str], ...], default: str) -> dict[str, Any]:
    """Da-Bubble Select. Prop shape follows the widget-builder spec — centralised
    here so a spec change is a one-line fix."""
    return {
        "type": "Select",
        "name": name,
        "size": "md",
        "variant": "outline",
        "defaultValue": default,
        "value": default,
        "style": _field_style(),
        "options": [{"value": value, "label": label} for value, label in options],
    }


def identifikacia_widget(
    caption: str | None = None,
    *,
    with_type_select: bool = False,
) -> dict[str, Any]:
    """Identification widget.

    Default (first render) is a single free-text input — the customer just types
    their identifier and the classifier figures out the type. When the classifier
    *cannot* disambiguate the type, the dispatcher re-renders with
    ``with_type_select=True``: the "Typ údaja" dropdown is added (default ``auto``)
    together with an explanation of why it appeared.
    """
    inner: list[dict[str, Any]] = [
        {
            "type": "Title",
            "value": "Identifikácia",
            "size": "md",
            "weight": "bold",
            "style": {"margin": "0", "color": _INK},
        },
        {
            "type": "Caption",
            "value": caption or "Zadajte jeden z vašich identifikačných údajov.",
            "size": "sm",
            "style": {"margin": "0", "color": _MUTED},
        },
        {
            "type": "Col",
            "gap": 4,
            "width": "100%",
            "children": [
                {"type": "Label", "fieldName": IDENT_INPUT_KEY, "value": "Identifikačný údaj"},
                {
                    "type": "Input",
                    "name": IDENT_INPUT_KEY,
                    "inputType": "text",
                    "required": True,
                    "placeholder": "napr. 0902 804 660, IČO, rodné číslo, kód zákazníka…",
                    "variant": "outline",
                    "size": "md",
                    "pattern": "^.{3,}$",
                    "style": _field_style(),
                    "errorMessages": {
                        "required": "Zadajte, prosím, identifikačný údaj.",
                        "validation": "Zadajte aspoň 3 znaky.",
                    },
                },
            ],
        },
    ]

    if with_type_select:
        inner.append(
            {
                "type": "Col",
                "gap": 4,
                "width": "100%",
                "children": [
                    {"type": "Label", "fieldName": IDENT_TYPE_KEY, "value": "Typ údaja"},
                    {
                        "type": "Caption",
                        "value": (
                            "Zadaný údaj sa dal rozpoznať viacerými spôsobmi — vyberte, "
                            "prosím, o aký typ ide (alebo nechajte „Automaticky rozpoznať“)."
                        ),
                        "size": "sm",
                        "style": {"margin": "0", "color": _MUTED},
                    },
                    _select(IDENT_TYPE_KEY, IDENT_TYPE_OPTIONS, default="auto"),
                ],
            }
        )

    inner.append(_primary_button("POKRAČOVAŤ", IDENT_SUBMIT_UTTERANCE))

    return {
        "type": "Form",
        "gap": 18,
        "padding": 24,
        "align": "start",
        "radius": "xl",
        "background": "white",
        "style": {"boxShadow": "0 12px 30px rgba(0,0,0,0.12)"},
        "children": [
            {
                "type": "Col",
                "gap": 14,
                "width": "100%",
                "style": {"maxWidth": "520px"},
                "children": inner,
            },
        ],
    }


_AUTH_FACTOR_UI = {
    "name": {
        "title": "Overenie totožnosti",
        "label": "Meno a priezvisko",
        "placeholder": "Jana Nováková",
        "input_type": "text",
        "pattern": r"^.{2,}$",
        "validation_msg": "Zadajte meno a priezvisko.",
    },
    "kod_adresata": {
        "title": "Overenie totožnosti",
        "label": "Kód adresáta (z faktúry)",
        "placeholder": "napr. 4482259101",
        "input_type": "text",
        "pattern": r"^\d{6,12}$",
        "validation_msg": "Kód adresáta nájdete na faktúre — len cifry.",
    },
    "rc_last4": {
        "title": "Overenie totožnosti",
        "label": "Posledné 4 cifry rodného čísla",
        "placeholder": "1234",
        "input_type": "tel",
        "pattern": r"^\d{4}$",
        "validation_msg": "Zadajte presne 4 cifry.",
    },
}


def auth_factor_widget(factor: str, *, caption: str | None = None) -> dict[str, Any]:
    """Authentication widget for a single factor + a skip ("Nemám / Neviem nájsť") button."""
    ui = _AUTH_FACTOR_UI[factor]
    field_key = AUTH_FIELD_KEYS[factor]
    return {
        "type": "Form",
        "gap": 18,
        "padding": 24,
        "align": "start",
        "radius": "xl",
        "background": "white",
        "style": {"boxShadow": "0 12px 30px rgba(0,0,0,0.12)"},
        "children": [
            {
                "type": "Col",
                "gap": 14,
                "width": "100%",
                "style": {"maxWidth": "520px"},
                "children": [
                    {
                        "type": "Title",
                        "value": ui["title"],
                        "size": "md",
                        "weight": "bold",
                        "style": {"margin": "0", "color": _INK},
                    },
                    {
                        "type": "Caption",
                        "value": caption or "Zadajte, prosím, požadovaný overovací údaj.",
                        "size": "sm",
                        "style": {"margin": "0", "color": _MUTED},
                    },
                    {
                        "type": "Col",
                        "gap": 4,
                        "width": "100%",
                        "children": [
                            {"type": "Label", "fieldName": field_key, "value": ui["label"]},
                            {
                                "type": "Input",
                                "name": field_key,
                                "inputType": ui["input_type"],
                                "required": True,
                                "placeholder": ui["placeholder"],
                                "variant": "outline",
                                "size": "md",
                                "pattern": ui["pattern"],
                                "style": _field_style(),
                                "errorMessages": {
                                    "required": ui["validation_msg"],
                                    "validation": ui["validation_msg"],
                                },
                            },
                        ],
                    },
                    _primary_button("POKRAČOVAŤ", AUTH_SUBMIT_UTTERANCE),
                    _ghost_button("Nemám / Neviem nájsť", AUTH_SKIP_UTTERANCE),
                ],
            },
        ],
    }
