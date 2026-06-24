"""MCP tools for mcp_telekom_identity.

identifikacia(hodnota, typ)
— Single entry point: identify a Telekom customer by any supported identifier
  (telefón / IČO / rodné číslo / kód zákazníka / sériové číslo). Auto-detects the
  type via the classifier, or renders an identification widget in chat channels.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request

import httpx
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from lib.bubble_widgets import bubble_widget_result
from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
from lib.mcp_service.state import TTLStore
from svc.mcp_telekom_identity import widgets
from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)

if TYPE_CHECKING:
    from svc.mcp_telekom_identity.dps_get_client import DPSGetClient


_RC_PATTERN = re.compile(r"^\d{9,10}$")
_RC_INVALID_MESSAGE = (
    "Rodné číslo nie je v správnom tvare. Zadajte ho ako 9 alebo 10 cifier bez lomky."
)
_RC_NOT_FOUND_MESSAGE = "Zákazníka s týmto rodným číslom sa nepodarilo nájsť."
_RC_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa rodného čísla. Po úspechu vráti meno "
    "(alebo zoznam mien ak je záznamov viac). Interné identifikátory "
    "a kontakty si tool uloží do pamäte konverzácie pre ďalšie nástroje "
    "— netreba ich od neho znova žiadať."
)

# MSISDN normalization:
# - strip whitespace, dashes, parentheses, dots, leading "+"
# - "0904..." (SK local, 10 digits)  → "421904..."   (replace leading 0 with 421)
# - "00421904..."                    → "421904..."   (strip 00)
# - "+421904..." or "421904..."      → "421904..."   (already intl or with +)
_MSISDN_STRIP_RE = re.compile(r"[\s\-().+]")


def _normalize_msisdn(raw: str) -> str | None:
    """Return canonical MSISDN form ``421XXXXXXXXX`` (12 digits, no +) or None if not valid SK MSISDN."""
    cleaned = _MSISDN_STRIP_RE.sub("", raw or "")
    if not cleaned.isdigit():
        return None
    if cleaned.startswith("00421"):
        cleaned = cleaned[2:]  # 00421... → 421...
    elif cleaned.startswith("0") and len(cleaned) == 10:
        cleaned = "421" + cleaned[1:]  # 0904... → 421904...
    # Now must match SK intl format: 421 + 9 digits (mobile starts with 9, but accept
    # other prefixes for fixed-line MSISDN too)
    if re.fullmatch(r"421\d{9}", cleaned):
        return cleaned
    return None


def _normalize_serial(raw: str) -> str | None:
    """Return canonical serial number (uppercase, no separators) or None if invalid format."""
    cleaned = _SERIAL_STRIP_RE.sub("", raw or "").upper()
    if _SERIAL_PATTERN.fullmatch(cleaned):
        return cleaned
    return None


# Slovak IČO: exactly 8 digits.
_ICO_PATTERN = re.compile(r"^\d{8}$")
_ICO_INVALID_MESSAGE = "IČO nie je v správnom tvare. Zadajte ho ako 8 cifier."
_ICO_NOT_FOUND_MESSAGE = "Spoločnosť s týmto IČO sa nepodarilo nájsť."
_ICO_TOOL_DESCRIPTION = (
    "Identifikuj firemného zákazníka podľa IČO. Po úspechu vráti názov spoločnosti "
    "(alebo zoznam názvov ak je záznamov viac). Interné identifikátory a kontakty "
    "si tool uloží do pamäte konverzácie pre ďalšie nástroje — netreba ich od neho "
    "znova žiadať."
)

_log = logging.getLogger(__name__)

_NLP_BASE_URL = os.environ.get("APP_GOODBOT_URL") or os.environ.get(
    "GOODBOT_URL", "http://goodbot.internal-test.svc.cluster.local:8121"
)
_NLP_TIMEOUT_SECONDS = 1.0

# Widget-submitted raw inputs land in the mirror (the host writes them into
# named_entities on submit). They are never pushed back to the NLP engine — they
# may contain sensitive identifiers (rodné číslo, auth secrets). We read them,
# use them, and let them expire with the mirror; the LLM never sees them.
_DO_NOT_FLUSH_KEYS = frozenset(
    {
        "identifikacia_vstup",
        "identifikacia_typ",
        "autentifikacia_meno_priezvisko",
        "autentifikacia_kod_adresata",
        "autentifikacia_rc_last4",
    }
)


def _nlp_set_state(conversation_id: str, named_entities: dict[str, Any]) -> None:
    """Record named_entities written *by our tools*.

    Two stores are updated:

    - :data:`_NLP_MIRROR_STATE` — the read mirror, so later reads in the same turn
      see the tool's write (alongside whatever was GET-ed from the NLP engine).
    - :data:`_NLP_PENDING_STATE` — the *only* thing :func:`_nlp_flush` pushes back.
      We push exactly the entities our tools created/changed, never the large set
      of state GET-ed from the NLP engine (gpt_history, channel, …).
    """
    if not conversation_id:
        return
    # Coerce to str; named_entities only carries scalars.
    coerced = {k: str(v) for k, v in named_entities.items()}

    current = _NLP_MIRROR_STATE.get(conversation_id) or {}
    current.update(coerced)
    _NLP_MIRROR_STATE.set(conversation_id, current)

    pending = _NLP_PENDING_STATE.get(conversation_id) or {}
    pending.update(coerced)
    _NLP_PENDING_STATE.set(conversation_id, pending)


def _nlp_get_named_entities(conversation_id: str) -> dict[str, str]:
    """Read mirrored NLP named_entities for the conversation."""
    if not conversation_id:
        return {}
    return _NLP_MIRROR_STATE.get(conversation_id) or {}


def _consume_named_entity(conversation_id: str, key: str) -> str | None:
    """Read and remove a named_entity from the mirror (one-shot widget input).

    Widget-submitted values are consumed once so a later call doesn't re-apply a
    stale value (e.g. re-verifying an already-handled auth factor).
    """
    if not conversation_id:
        return None
    current = _NLP_MIRROR_STATE.get(conversation_id)
    if not current or key not in current:
        return None
    value = current.pop(key)
    _NLP_MIRROR_STATE.set(conversation_id, current)
    return value


async def _nlp_load(conversation_id: str) -> None:
    """Populate local mirror from NLP engine if not already cached.

    Falls back gracefully — 400/404 (no session) and network errors are logged
    at DEBUG/WARNING and do not raise. Empty mirror after both sources is fine.
    """
    if not conversation_id:
        return
    if _NLP_MIRROR_STATE.get(conversation_id):
        return  # already warm

    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/named_entities"
    try:
        async with httpx.AsyncClient(timeout=_NLP_TIMEOUT_SECONDS) as http:
            resp = await http.get(url)
        if resp.status_code == 200:
            entities = {
                k: str(v)
                for k, v in (resp.json().get("named_entities") or {}).items()
            }
            if entities:
                _NLP_MIRROR_STATE.set(conversation_id, entities)
                _log.info("NLP GET named_entities %s -> loaded %d entities", url, len(entities))
        elif resp.status_code in (400, 404):
            _log.debug("NLP GET named_entities %s -> %s (no session)", url, resp.status_code)
        else:
            _log.warning("NLP GET named_entities %s -> %s", url, resp.status_code)
    except Exception as exc:
        _log.warning("NLP GET named_entities %s -> error: %s", url, exc)


def _nlp_flush(conversation_id: str) -> None:
    """Fire-and-forget PUT of the entities *our tools wrote* to the NLP engine.

    We push exactly :data:`_NLP_PENDING_STATE` — the named_entities accumulated by
    :func:`_nlp_set_state` — and nothing else. No delta against the GET-ed state is
    computed, so the large set of conversation state mirrored from the NLP engine
    (gpt_history, channel, …) is never echoed back.

    Sensitive widget inputs (:data:`_DO_NOT_FLUSH_KEYS`) are filtered out as a
    safety net. On a successful PUT the pushed keys are removed from the pending
    buffer; on failure they stay and are retried on the next flush. Retries up to
    3 times on 429 honouring Retry-After. Errors are logged and never raised.
    """
    if not conversation_id:
        return
    pending = dict(_NLP_PENDING_STATE.get(conversation_id) or {})
    to_push = {k: v for k, v in pending.items() if k not in _DO_NOT_FLUSH_KEYS}
    if not to_push:
        return

    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/states"
    payload = {"named_entities": to_push}
    body = json.dumps(payload, ensure_ascii=False).encode()

    def _do() -> None:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(
                url, data=body, method="PUT",
                headers={"Content-Type": "application/json"},
            )
            _log.info("NLP state PUT %s named_entities=%s", url, json.dumps(payload, ensure_ascii=False))
            try:
                with urllib.request.urlopen(req, timeout=_NLP_TIMEOUT_SECONDS) as resp:
                    _log.info("NLP state PUT %s -> HTTP %s", url, resp.status)
                    # Drop the pushed keys from the pending buffer (keep any added since).
                    remaining = {
                        k: v
                        for k, v in (_NLP_PENDING_STATE.get(conversation_id) or {}).items()
                        if k not in to_push
                    }
                    _NLP_PENDING_STATE.set(conversation_id, remaining)
                    return
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < max_retries:
                    retry_after = float(
                        exc.headers.get("Retry-After") or exc.headers.get("retry-after") or "1"
                    )
                    _log.warning(
                        "NLP state PUT %s -> 429, retry in %.1fs (%d/%d)",
                        url, retry_after, attempt, max_retries,
                    )
                    time.sleep(retry_after)
                else:
                    _log.warning("NLP state PUT %s -> HTTP %s", url, exc.code)
                    return
            except Exception as exc:
                _log.warning("NLP state PUT %s -> error: %s", url, exc)
                return

    threading.Thread(target=_do, daemon=True, name="nlp-state-put-identity").start()


# "Kód zákazníka" or "Kód účtu" — numeric code 8-12 digits, no separators.
# Ends in 0  → Customer ID
# Ends in 1-9 → Billing Account ID (resolves to Customer via billingAccount.customer.id)
_KOD_PATTERN = re.compile(r"^\d{8,12}$")
_KOD_INVALID_MESSAGE = "Kód zákazníka má tvar 8 až 12 cifier (napr. 4482259100)."
_KOD_NOT_FOUND_MESSAGE = "Zákazníka s týmto kódom sa nepodarilo nájsť."
_KOD_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa číselného kódu — môže byť kód zákazníka "
    "(končí cifrou 0) alebo kód fakturačného účtu (končí inou cifrou). "
    "Po úspechu vráti meno zákazníka. Interné identifikátory si tool uloží "
    "do pamäte konverzácie pre ďalšie nástroje."
)

_TEL_INVALID_MESSAGE = (
    "Telefónne číslo nie je v správnom tvare. Zadajte ho ako 0904... (10 cifier) "
    "alebo +421904... (medzinárodný tvar)."
)
_TEL_NOT_FOUND_MESSAGE = "Zákazníka s týmto telefónnym číslom sa nepodarilo nájsť."
_TEL_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa telefónneho čísla (mobilné aj pevné). "
    "Akceptuje slovenský tvar (0904...), medzinárodný tvar (+421904... alebo 421904...). "
    "Po úspechu vráti meno zákazníka. Interné identifikátory si tool uloží "
    "do pamäte konverzácie pre ďalšie nástroje."
)

# Serial number normalization: strip whitespace, dashes, slashes, dots; uppercase.
# Then validate as alphanumeric 8-30 chars. Real test data is 12 chars but production
# routers/STBs use a wide variety of lengths and formats — keep the pattern permissive.
_SERIAL_STRIP_RE = re.compile(r"[\s\-/.]")
_SERIAL_PATTERN = re.compile(r"^[A-Z0-9]{8,30}$")
_SERIAL_INVALID_MESSAGE = (
    "Sériové číslo nie je v správnom tvare. Zadajte ho ako 8 až 30 alfanumerických "
    "znakov (napr. M91450EB0603)."
)
_SERIAL_NOT_FOUND_MESSAGE = "Zákazníka s týmto sériovým číslom sa nepodarilo nájsť."
_SERIAL_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa sériového čísla zariadenia "
    "(router, set-top box, modem, …). Po úspechu vráti meno zákazníka. "
    "Interné identifikátory si tool uloží do pamäte konverzácie pre ďalšie nástroje."
)

# Single identification entry point — auto-detects the identifier type.
# NOTE: this tool only PROCESSES an identifier; it never renders a widget. To show
# the form use zobraz_identifikacny_widget.
_IDENTIFIKACIA_TOOL_DESCRIPTION = (
    "SPRACUJ identifikačný údaj zákazníka a identifikuj ho "
    "(telefónne číslo, IČO, rodné číslo, kód zákazníka/fakturačného účtu, sériové číslo). "
    "Typ údaja rozpozná tool sám podľa formátu. Po úspechu vráti meno zákazníka a interné "
    "identifikátory si uloží do pamäte konverzácie pre ďalšie nástroje.\n"
    "KEDY volať: (1) keď už hodnotu máš → zavolaj s hodnota=<údaj>; (2) keď zákazník odoslal "
    "identifikačný widget (utterance 'identifikacia_widget_submitted') → zavolaj BEZ parametrov, "
    "hodnotu si prečíta z pamäte konverzácie. "
    "Na ZOBRAZENIE formulára tento tool NEVOLAJ — na to slúži zobraz_identifikacny_widget."
)

_CHANNEL_KEY = "Channel"
_CHANNEL_CHAT = "chat"

# Identifier route keys (also the dropdown values in the widget).
_TYPE_TELEFON = "telefon"
_TYPE_ICO = "ico"
_TYPE_RODNE_CISLO = "rodne_cislo"
_TYPE_KOD_ZAKAZNIKA = "kod_zakaznika"
_TYPE_SERIOVE_CISLO = "seriove_cislo"
_TYPE_AUTO = "auto"

_IDENT_INPUT_REQUIRED_MESSAGE = (
    "Potrebujem identifikačný údaj — napríklad telefónne číslo, rodné číslo, "
    "IČO, kód zákazníka alebo sériové číslo zariadenia."
)
_IDENT_UNRECOGNIZED_MESSAGE = (
    "Tento údaj sa mi nepodarilo rozpoznať. Skúste, prosím, telefónne číslo, rodné číslo, "
    "IČO, kód zákazníka alebo sériové číslo zariadenia."
)
_IDENT_AMBIGUOUS_MESSAGE = (
    "Zadaný údaj sa dal rozpoznať viacerými spôsobmi — potrebujem, aby zákazník vybral typ."
)

_IDENT_WIDGET_TOOL_DESCRIPTION = (
    "ZOBRAZ zákazníkovi identifikačný formulár (widget) v chat kanáli a požiadaj ho o "
    "identifikačný údaj. Prvotný formulár má len jedno pole; typ údaja rozpozná tool sám. "
    "TOTO JE VÝSTUP PRE ZÁKAZNÍKA A UKONČUJE TVOJ ŤAH: po zavolaní tohto toolu už NEVOLAJ "
    "žiadny ďalší nástroj a počkaj, kým zákazník formulár odošle. Až po jeho odoslaní "
    "(utterance 'identifikacia_widget_submitted') zavolaj identifikacia(). "
    "Mimo chat kanála widget nemá zmysel — vtedy vypýtaj údaj textom."
)

_AUTH_WIDGET_TOOL_DESCRIPTION = (
    "ZOBRAZ zákazníkovi autentifikačný formulár (widget) pre aktuálny overovací faktor v chat "
    "kanáli. Faktor sa určí automaticky z priebehu overenia (voliteľne zadaj faktor: "
    "meno_priezvisko / kod_adresata / rc_last4). Vyžaduje predchádzajúcu identifikáciu. "
    "TOTO JE VÝSTUP PRE ZÁKAZNÍKA A UKONČUJE TVOJ ŤAH: po zavolaní už NEVOLAJ žiadny ďalší "
    "nástroj a počkaj na odpoveď. Až po odoslaní zavolaj autentifikacia()."
)


def _is_valid_ico(digits: str) -> bool:
    """Slovak/Czech IČO checksum: 8 digits, weighted mod-11 over the first 7."""
    if len(digits) != 8 or not digits.isdigit():
        return False
    weights = (8, 7, 6, 5, 4, 3, 2)
    total = sum(int(digits[i]) * weights[i] for i in range(7))
    check = 11 - (total % 11)
    if check == 10:
        check = 0
    elif check == 11:
        check = 1
    return check == int(digits[7])


def _is_valid_rodne_cislo(digits: str) -> bool:
    """Structural RČ validation: 9 or 10 digits, plausible YYMMDD, and (10-digit) mod 11.

    Month accepts the standard 1-12 and +50 (women). The rare post-2003 +20/+70
    extensions are intentionally NOT accepted — they widen the false-positive
    surface (customer/billing codes that happen to parse as a date) more than
    they help. The mod-11 divisibility check (10-digit RČ only) is the main guard
    that keeps a random customer code from being mistaken for a rodné číslo.
    """
    if not digits.isdigit() or len(digits) not in (9, 10):
        return False
    month = int(digits[2:4])
    if month > 50:
        month -= 50
    if not 1 <= month <= 12:
        return False
    day = int(digits[4:6])
    if not 1 <= day <= 31:
        return False
    if len(digits) == 10 and int(digits) % 11 != 0:
        return False
    return True


def _classify_identifier(raw: str) -> tuple[str | None, list[str]]:
    """Classify a free-text identifier into a route key.

    Returns ``(type, [])`` for a confident single match, ``(None, candidates)``
    when two *structured* signals genuinely collide (caller should disambiguate),
    or ``(None, [])`` when nothing plausible matches.

    Priority: a structurally valid serial / phone / IČO / rodné číslo wins;
    ``kod_zakaznika`` (8-12 digits, no checksum) is only the fallback when no
    structured signal fires.
    """
    value = (raw or "").strip()
    if not value:
        return None, []

    # 1. Contains a letter → only the serial number is alphanumeric.
    serial_norm = _SERIAL_STRIP_RE.sub("", value).upper()
    if any(c.isalpha() for c in serial_norm):
        if _SERIAL_PATTERN.fullmatch(serial_norm):
            return _TYPE_SERIOVE_CISLO, []
        return None, []

    # 2. Digits only from here.
    stripped = _MSISDN_STRIP_RE.sub("", value)
    if not stripped or not stripped.isdigit():
        return None, []

    msisdn = _normalize_msisdn(value)
    explicit_phone = msisdn is not None and (
        value.strip().startswith("+")
        or stripped.startswith("00421")
        or stripped.startswith("421")
    )
    if explicit_phone:
        return _TYPE_TELEFON, []

    digits = stripped
    n = len(digits)

    strong: list[str] = []
    if _is_valid_rodne_cislo(digits):
        strong.append(_TYPE_RODNE_CISLO)
    if n == 8 and _is_valid_ico(digits):
        strong.append(_TYPE_ICO)
    if msisdn is not None:  # bare local 0… that normalises to a SK MSISDN
        strong.append(_TYPE_TELEFON)

    if len(strong) == 1:
        return strong[0], []
    if len(strong) >= 2:
        return None, strong  # genuine collision (e.g. 09… valid as phone and RČ)

    # No structured signal → numeric customer/billing code is the fallback.
    if _KOD_PATTERN.fullmatch(digits):
        return _TYPE_KOD_ZAKAZNIKA, []
    return None, []

_IDENTITY_TTL_SECONDS = 30 * 60
_IDENTITY_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=_IDENTITY_TTL_SECONDS)

# Mirror of named_entities we PUT to NLP, plus externally-set entities like
# input_source and authentication_type. In production we will (Q3 in
# docs/OPEN_QUESTIONS.md) replace this read path with a GET /named_entities
# call against the real NLP engine. For now it lets tests and the
# `nastav_test_kontext` debug tool simulate that state without a real NLP.
_NLP_MIRROR_TTL_SECONDS = 30 * 60
_NLP_MIRROR_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)

# named_entities written by our tools that still need to be pushed to the NLP
# engine. _nlp_flush PUTs exactly this (never the GET-ed conversation state) and
# clears the pushed keys on success. Same TTL as the mirror.
_NLP_PENDING_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)

# Authentication
_AUTH_TTL_SECONDS = 30 * 60
_AUTH_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=_AUTH_TTL_SECONDS)

_MAX_AUTH_ATTEMPTS_PER_FACTOR = 3

# Factor names (use as dict keys + in factors_satisfied set + as "next_factor" value)
_FACTOR_TRUSTED_SOURCE = "trusted_source"
_FACTOR_NAME = "name"
_FACTOR_KOD_ADRESATA = "kod_adresata"
_FACTOR_RC_LAST4 = "rc_last4"
_FACTOR_ORDER = (
    _FACTOR_TRUSTED_SOURCE,
    _FACTOR_NAME,
    _FACTOR_KOD_ADRESATA,
    _FACTOR_RC_LAST4,
)

_AUTH_TYPE_STANDARD = "standard"
_AUTH_TYPE_SENSITIVE = "sensitive"

_AUTH_TOOL_DESCRIPTION = (
    "Overí totožnosť zákazníka. Volaj OPAKOVANE — pri každom volaní zaeviduje "
    "aktuálny faktor a vráti, čo treba ešte. Najprv treba zavolať jeden z "
    "identifikacia_* toolov (autentifikacia vychádza z výsledku identifikácie). "
    "Faktory sa pýtajú v pevnom poradí: 1) dôveryhodný zdroj (automaticky z volania), "
    "2) meno a priezvisko, 3) kód adresáta z faktúry, 4) posledné 4 cifry rodného čísla. "
    "Pre štandardné transakcie treba 2 faktory, pre citlivé 3 faktory. "
    "Bez parametrov tool vyhodnotí aktuálny stav a vráti ďalší krok. "
    "Ak zákazník daný údaj nemá, zavolaj s skip_current_factor=true."
)

_TEST_KONTEXT_DESCRIPTION = (
    "DEBUG/TEST IBA: nastaví hodnoty v lokálnej mirror cache, ktoré inak prichádzajú "
    "z NLP engine cez named_entities (input_source = telefón/email zákazníka z caller ID, "
    "authentication_type = 'standard' alebo 'sensitive' podľa zámeru transakcie). "
    "V produkčnom prostredí tieto hodnoty plne setuje NLP engine."
)

_UPSTREAM_ERROR_MESSAGE = "Vyskytol sa technický problém. Prepojím vás na operátora."

# Agreement statuses considered "active" — mirrors NLP extractor get_agreements.py.
# terminated/cancelled/expired are intentionally excluded: a contract may still have
# an agreementPeriod.endDateTime in the future even after early termination.
_ACTIVE_AGREEMENT_STATUSES = frozenset({
    "active",
    "inProtectionPeriod",
    "validated",
    "signed",
    "approved",
    "inProcess",
    "inCorrection",
    "initialized",
    "approvalPending",
})

_VIAZANOST_TYP_ORDER = (
    "Prolongacne_okno",
    "Viazanost_do_roka",
    "Viazanost_viac_ako_rok",
    "Chyba",
)

_VIAZANOST_TOOL_DESCRIPTION = (
    "Overí aktívne zmluvy (viazanosti) identifikovaného a autentifikovaného zákazníka "
    "z Product Inventory DPS. Vyžaduje predchádzajúcu identifikáciu (identifikacia_*) "
    "a štandardnú autentifikáciu (autentifikacia). Vráti klasifikáciu viazanosti "
    "(Nema_viazanost / Prolongacne_okno / Viazanost_do_roka / Viazanost_viac_ako_rok), "
    "surové dáta zmlúv a odporúčanú odpoveď pre zákazníka."
)

# Methods whose input is PII (sent to NLP only as last4=XXXX marker).
_PII_METHODS = frozenset({"rodne_cislo"})


def _persist_identification(
    *,
    conversation_id: str,
    method: str,
    value: str,
    candidates: list[dict[str, Any]],
) -> None:
    """Cache identification result + push named_entities update to the NLP engine.

    PII methods (rodne_cislo, op, pas) push only ``last4=XXXX`` to NLP.
    Non-PII methods (ico, kod_zakaznika, telefon, seriove_cislo) push the
    full normalized value. The cache itself always holds the full value
    (process-local, 30 min TTL).
    """
    if not conversation_id:
        _log.warning("identifikacia_%s: no conversation_id — cache and NLP skipped", method)
        return

    _IDENTITY_STATE.set(
        conversation_id,
        {
            "rc_last4": value[-4:],
            "identification_method": method,
            "identification_value": value,
            "candidates": candidates,
        },
    )

    nlp_value = f"last4={value[-4:]}" if method in _PII_METHODS else value

    _nlp_set_state(
        conversation_id,
        {
            "identification_method": method,
            "identification": nlp_value,
        },
    )


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _format_address(addr: dict[str, Any]) -> str:
    street = addr.get("streetName") or ""
    nr = addr.get("streetNr") or ""
    postcode = addr.get("postcode") or ""
    locality = addr.get("locality") or addr.get("city") or ""
    street_part = " ".join(part for part in (street, nr) if part).strip()
    postcode_part = " ".join(part for part in (postcode, locality) if part).strip()
    return ", ".join(part for part in (street_part, postcode_part) if part)


def _normalize_contacts(party_contacts: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for c in party_contacts:
        ctype = c.get("type")
        medium = c.get("medium") or {}
        if ctype == "mobile" and medium.get("number"):
            out.append({"type": "mobile", "value": str(medium["number"])})
        elif ctype == "email" and medium.get("emailAddress"):
            out.append({"type": "email", "value": str(medium["emailAddress"])})
        elif ctype == "address" and isinstance(medium.get("address"), dict):
            formatted = _format_address(medium["address"])
            if formatted:
                out.append({"type": "address", "value": formatted})
    return out


def _normalize_identifications(idents: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ident in idents or []:
        itype = ident.get("type")
        if itype == "socialSecurityNumber":
            continue  # Never echo RČ back to the caller.
        iid = ident.get("identificationId")
        if isinstance(itype, str) and isinstance(iid, str):
            out.append({"type": itype, "id": iid})
    return out


def _treatment_package(customer: dict[str, Any]) -> str | None:
    for ch in customer.get("characteristics") or []:
        if ch.get("name") == "treatmentPackage":
            value = ch.get("value")
            return str(value) if value is not None else None
    return None


def _valid_for(customer: dict[str, Any]) -> dict[str, str | None] | None:
    vf = customer.get("validFor")
    if not isinstance(vf, dict):
        return None
    return {
        "start": vf.get("startDateTime"),
        "end": vf.get("endDateTime"),
    }


def _extract_billing_account_ids(customer: dict[str, Any] | None) -> list[str]:
    """Collect string-valued BillingAccount.id from a Customer record."""
    if not customer:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for ca in customer.get("customerAccounts") or []:
        for ba in ca.get("billingAccounts") or []:
            bid = ba.get("id")
            if isinstance(bid, str) and bid not in seen:
                seen.add(bid)
                out.append(bid)
    return out


def _extract_rc_last4_from_party(party: dict[str, Any]) -> str | None:
    """Return the last 4 chars of the party's socialSecurityNumber identification, or None."""
    ind = party.get("individual") or {}
    for ident in ind.get("individualIdentifications") or []:
        if ident.get("type") == "socialSecurityNumber":
            value = ident.get("identificationId")
            if isinstance(value, str) and len(value) >= 4:
                return value[-4:]
    return None


def _candidate(
    party: dict[str, Any],
    customer: dict[str, Any] | None,
) -> dict[str, Any]:
    ind = party.get("individual") or {}
    org = party.get("organization") or {}
    party_type = party.get("type")

    given = ind.get("givenName") or ""
    family = ind.get("familyName") or ""

    if party_type == "organization":
        name = org.get("tradingName") or org.get("name") or party.get("name") or None
        identifications_src = org.get("organizationIdentifications") or []
    else:
        name = " ".join(part for part in (given, family) if part) or None
        identifications_src = ind.get("individualIdentifications") or []

    return {
        "party_id": party.get("id"),
        "customer_id": (customer or {}).get("id"),
        "name": name,
        "given_name": given or None,
        "family_name": family or None,
        "status": (customer or {}).get("status"),
        "market_segment": (customer or {}).get("marketSegment"),
        "customer_segment": (customer or {}).get("customerSegment"),
        "treatment_package": _treatment_package(customer) if customer else None,
        "valid_for": _valid_for(customer) if customer else None,
        "contacts": _normalize_contacts(party.get("contacts") or []),
        "identifications": _normalize_identifications(identifications_src),
        "billing_account_ids": _extract_billing_account_ids(customer),
        "auth_rc_last4": _extract_rc_last4_from_party(party),
    }


def _customer_display_name(customer: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    """Return (display_name, given_name, family_name) from a Customer.name field.

    B2C customers store name as "Surname,FirstName" (comma, no space after) — reverse it.
    B2B customers may have legitimate commas in company names (e.g. "J A L & Š, S. R. O.")
    — those must NOT be reversed.
    """
    raw = (customer.get("name") or "").strip()
    if not raw:
        return None, None, None
    segment = customer.get("customerSegment")
    # Reverse only when B2C AND the name has the "Surname,FirstName" pattern
    # (single comma, no whitespace immediately after the comma).
    if segment == "B2C" and raw.count(",") == 1:
        family, given = raw.split(",", 1)
        if given and not given[0].isspace():
            return f"{given} {family}".strip(), given.strip(), family.strip()
    return raw, None, None


def _candidate_from_customer(customer: dict[str, Any]) -> dict[str, Any]:
    """Build a candidate from a Customer record alone (no Party fetched).

    Party-derived fields (contacts, identifications) are empty — downstream tools that
    need them can fetch the Party via the cached `party_id`.
    """
    display, given, family = _customer_display_name(customer)
    party_id = (customer.get("engagedParty") or {}).get("id")
    return {
        "party_id": party_id,
        "customer_id": customer.get("id"),
        "name": display,
        "given_name": given,
        "family_name": family,
        "status": customer.get("status"),
        "market_segment": customer.get("marketSegment"),
        "customer_segment": customer.get("customerSegment"),
        "treatment_package": _treatment_package(customer),
        "valid_for": _valid_for(customer),
        "contacts": [],
        "identifications": [],
        "billing_account_ids": _extract_billing_account_ids(customer),
        "auth_rc_last4": None,
    }


def _dps_error_payload(exc: DPSError) -> dict[str, Any]:
    if isinstance(exc, DPSAuthError):
        return {
            "found": False,
            "error": "auth_failed",
            "message": _UPSTREAM_ERROR_MESSAGE,
        }
    if isinstance(exc, DPSTimeoutError):
        return {
            "found": False,
            "error": "upstream_timeout",
            "message": _UPSTREAM_ERROR_MESSAGE,
        }
    if isinstance(exc, DPSNetworkError):
        return {
            "found": False,
            "error": "upstream_unreachable",
            "message": _UPSTREAM_ERROR_MESSAGE,
        }
    if isinstance(exc, (DPSUpstreamError, DPSInvalidResponseError)):
        return {
            "found": False,
            "error": "upstream_error",
            "message": _UPSTREAM_ERROR_MESSAGE,
        }
    return {
        "found": False,
        "error": "upstream_error",
        "message": _UPSTREAM_ERROR_MESSAGE,
    }


def _strip_diacritics(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c))


def _normalize_name_for_match(s: str) -> str:
    """Lower-case, strip diacritics, collapse whitespace, sort tokens.

    'Stano Muziková' → 'muzikova stano'  (sorted tokens)
    'MUZIKOVÁ STANO' → 'muzikova stano'  (same)
    """
    if not s:
        return ""
    cleaned = _strip_diacritics(s).lower()
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if t]
    return " ".join(sorted(tokens))


def _check_name(provided: str, candidate_name: str | None) -> bool:
    if not candidate_name:
        return False
    return _normalize_name_for_match(provided) == _normalize_name_for_match(candidate_name)


def _check_kod_adresata(provided: str, billing_account_ids: list[str]) -> bool:
    val = (provided or "").strip()
    if not val:
        return False
    return val in billing_account_ids


def _check_rc_last4(provided: str, candidate_rc_last4: str | None) -> bool:
    if not candidate_rc_last4:
        return False
    digits = re.sub(r"\D", "", provided or "")
    if len(digits) < 4:
        return False
    return digits[-4:] == candidate_rc_last4


def _normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def _check_trusted_source(input_source: str, contacts: list[dict[str, str]]) -> bool:
    """Compare input_source against candidate contacts (mobile / email)."""
    if not input_source:
        return False
    src = input_source.strip()
    if not src:
        return False

    # Try MSISDN normalization first
    src_msisdn = _normalize_msisdn(src)
    src_email = _normalize_email(src) if "@" in src else None

    for c in contacts:
        ctype = c.get("type")
        value = c.get("value")
        if not value:
            continue
        if ctype == "mobile":
            contact_msisdn = _normalize_msisdn(value)
            if src_msisdn and contact_msisdn and src_msisdn == contact_msisdn:
                return True
        elif ctype == "email" and src_email and _normalize_email(value) == src_email:
            return True
    return False


def _credited_factors_from_identification(
    method: str,
    value: str,
    contacts: list[dict[str, str]],
    input_source: str,
) -> set[str]:
    """Compute which factors are automatically satisfied at the start of auth.

    - factor 1 (trusted_source): always re-evaluated against input_source vs contacts.
    - factor 3 (kod_adresata): satisfied if the identification was kod_zakaznika with
      a billing-account-shaped value (last digit 1-9, i.e. NOT customer id).
    - factor 4 (rc_last4): satisfied if the identification was rodne_cislo (caller proved
      knowledge of the full RČ, last 4 trivially covered).
    """
    credited: set[str] = set()

    if _check_trusted_source(input_source, contacts):
        credited.add(_FACTOR_TRUSTED_SOURCE)

    if method == "kod_zakaznika":
        v = (value or "").strip()
        if v and v[-1] != "0":
            credited.add(_FACTOR_KOD_ADRESATA)
        # ending in 0 → customer id, not kod_adresata; do NOT credit

    if method == "rodne_cislo":
        credited.add(_FACTOR_RC_LAST4)

    return credited


def _next_factor(blocked: set[str]) -> str | None:
    """Return the next factor (in fixed order 1→2→3→4) not in ``blocked``.

    ``blocked`` is the union of factors that are satisfied, failed, or skipped —
    anything we won't ask the customer about again.
    """
    for f in _FACTOR_ORDER:
        if f not in blocked:
            return f
    return None


def _required_factors(auth_type: str) -> int:
    return 3 if auth_type == _AUTH_TYPE_SENSITIVE else 2


def _suggested_response_for_factor(factor: str) -> str:
    return {
        _FACTOR_NAME: "Pre overenie totožnosti mi, prosím, povedzte vaše meno a priezvisko.",
        _FACTOR_KOD_ADRESATA: "Povedzte mi, prosím, kód adresáta — nájdete ho na vašej faktúre.",
        _FACTOR_RC_LAST4: "Povedzte mi, prosím, posledné 4 cifry vášho rodného čísla.",
        _FACTOR_TRUSTED_SOURCE: "Pre overenie totožnosti mi povedzte ďalšiu informáciu.",
    }.get(factor, "Potrebujem ďalší overovací údaj.")


def _instruction_for_factor(factor: str) -> str:
    param = {
        _FACTOR_NAME: "meno_priezvisko",
        _FACTOR_KOD_ADRESATA: "kod_adresata",
        _FACTOR_RC_LAST4: "rc_last4",
    }.get(factor)
    if param:
        return (
            f"Počkaj na odpoveď zákazníka a zavolaj autentifikacia s parametrom {param}=<odpoveď>. "
            f"Ak zákazník daný údaj nemá, zavolaj autentifikacia(skip_current_factor=True)."
        )
    return "Zavolaj autentifikacia bez parametrov pre opätovné vyhodnotenie."


def _need_factor_response(
    next_factor: str,
    *,
    auth_type: str,
    satisfied: set[str],
    required: int,
    channel: str,
) -> str:
    """Return the "need this factor next" evaluation as JSON (never a widget).

    Rendering is the job of ``zobraz_autentifikacny_widget``. In a chat channel the
    ``instruction`` tells the LLM to show that widget; otherwise to collect the value
    via a parameter.
    """
    if channel == _CHANNEL_CHAT and next_factor in widgets.AUTH_FIELD_KEYS:
        instruction = (
            "V chat kanáli zobraz zobraz_autentifikacny_widget a počkaj na odoslanie zákazníkom."
        )
    else:
        instruction = _instruction_for_factor(next_factor)
    return _json(
        {
            "authenticated": False,
            "level_required": auth_type,
            "factors_satisfied": sorted(satisfied),
            "factors_remaining": required - len(satisfied),
            "next_factor": next_factor,
            "suggested_response": _suggested_response_for_factor(next_factor),
            "instruction": instruction,
        }
    )


def _peek_next_auth_factor(conv: str) -> str | None:
    """Read-only: the next customer-facing auth factor, or None if not applicable.

    Mirrors ``autentifikacia``'s credit + blocked computation WITHOUT mutating any
    state. Returns ``name`` / ``kod_adresata`` / ``rc_last4``; returns None when
    there is no single identification, auth is already complete, or only the
    automatic ``trusted_source`` factor would remain.
    """
    identity = _IDENTITY_STATE.get(conv)
    if not identity:
        return None
    candidates = identity.get("candidates") or []
    if len(candidates) != 1:
        return None
    candidate = candidates[0]

    state = _AUTH_STATE.get(conv) or {}
    satisfied = set(state.get("factors_satisfied") or [])
    failed = set(state.get("factors_failed") or [])
    skipped = set(state.get("factors_skipped") or [])

    nlp = _nlp_get_named_entities(conv)
    input_source = nlp.get("input_source", "")
    auth_type = nlp.get("authentication_type") or _AUTH_TYPE_STANDARD
    if auth_type not in (_AUTH_TYPE_STANDARD, _AUTH_TYPE_SENSITIVE):
        auth_type = _AUTH_TYPE_STANDARD

    satisfied |= _credited_factors_from_identification(
        identity.get("identification_method") or "",
        identity.get("identification_value") or "",
        candidate.get("contacts") or [],
        input_source,
    )
    if len(satisfied) >= _required_factors(auth_type):
        return None  # already authenticated

    blocked = satisfied | failed | skipped
    if _FACTOR_TRUSTED_SOURCE not in satisfied and not input_source:
        blocked = blocked | {_FACTOR_TRUSTED_SOURCE}
    nxt = _next_factor(blocked)
    if nxt == _FACTOR_TRUSTED_SOURCE:  # automatic — the customer can't act on it
        nxt = _next_factor(blocked | {_FACTOR_TRUSTED_SOURCE})
    return nxt if nxt in widgets.AUTH_FIELD_KEYS else None


def _persist_auth_state(conv: str, state: dict[str, Any]) -> None:
    """Serialise sets to lists before storing in TTLStore (so retrieval is sane)."""
    serialised = dict(state)
    for key in ("factors_satisfied", "factors_failed", "factors_skipped"):
        if isinstance(serialised.get(key), set):
            serialised[key] = sorted(serialised[key])
    _AUTH_STATE.set(conv, serialised)


def _parse_agreement_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        return None


def _filter_active_agreements(
    agreements: list[Any], today: date
) -> list[dict[str, str]]:
    active: list[dict[str, str]] = []
    for agr in agreements:
        status = (agr.get("status") or "").lower()
        if status and status not in _ACTIVE_AGREEMENT_STATUSES:
            continue
        period = agr.get("agreementPeriod") or {}
        agr_from = _parse_agreement_date(period.get("startDateTime", ""))
        agr_to = _parse_agreement_date(period.get("endDateTime", ""))
        if agr_from is None or agr_to is None:
            continue
        if agr_from <= today <= agr_to:
            active.append({"from": str(agr_from), "to": str(agr_to)})
    return active


def _product_display_name(product: dict[str, Any]) -> tuple[str, str]:
    """Return (display_name, identifier) for a product.

    display_name  — customer-facing label, e.g. "Magio TV XL (1E104IBSH)"
    identifier    — publicIdentifier or productSerialNumber (raw, no label)
    """
    group = product.get("group") or ""
    product_name = product.get("name") or ""
    label = product.get("label") or ""

    # service_name: label is the customer-visible name; device fallback when label absent
    if label:
        service_name = label
    elif group == "device":
        service_name = f"Zariadenie: {product_name}" if product_name else "Zariadenie"
    else:
        service_name = product_name

    identifier = (
        product.get("publicIdentifier")
        or product.get("productSerialNumber")
        or ""
    )

    if identifier and identifier != service_name:
        display_name = f"{service_name} ({identifier})"
    else:
        display_name = service_name

    return display_name, identifier


def _parse_products_with_active_agreements(
    data: list[Any], today: date
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for product in data:
        if not isinstance(product, dict):
            continue
        raw_agreements = product.get("agreements") or []
        if not isinstance(raw_agreements, list):
            raw_agreements = []
        active = _filter_active_agreements(raw_agreements, today)
        if not active:
            continue

        display_name, identifier = _product_display_name(product)

        latest_to: date | None = None
        for agr in active:
            agr_to = _parse_agreement_date(agr.get("to", ""))
            if agr_to is not None and (latest_to is None or agr_to > latest_to):
                latest_to = agr_to

        result.append({
            "display_name": display_name,
            "group": product.get("group") or "",
            "identifier": identifier,
            "viazanost_do": str(latest_to) if latest_to else None,
        })
    return result


def _classify_viazanost(
    active_products: list[dict[str, Any]], today: date
) -> tuple[str, str, date | None]:
    """Return (viazanost_typ, suggested_response, latest_end_date).

    Classification mirrors MSG_LAC_SC_VIAZANOST in LAC Selfcare.yaml:
      Nema_viazanost         — no active agreements
      Prolongacne_okno       — latest end < 90 days away
      Viazanost_do_roka      — latest end 90–365 days away
      Viazanost_viac_ako_rok — latest end ≥ 365 days away
    Uses the LATEST end date across all products for the most conservative estimate.
    Builds a per-service breakdown in suggested_response when multiple services exist.
    """
    if not active_products:
        return (
            "Nema_viazanost",
            "Na Vami zadanom čísle aktuálne neevidujeme viazanosť.",
            None,
        )

    latest_to: date | None = None
    for product in active_products:
        agr_to = _parse_agreement_date(product.get("viazanost_do") or "")
        if agr_to is not None and (latest_to is None or agr_to > latest_to):
            latest_to = agr_to

    if latest_to is None:
        return "Chyba", "Mrzí ma to, nepodarilo sa mi získať údaje o viazanosti.", None

    days_left = (latest_to - today).days

    # Build per-service lines for the suggested response
    service_lines: list[str] = []
    for p in active_products:
        s_display = p.get("display_name") or "Neznáma služba"
        s_date = _parse_agreement_date(p.get("viazanost_do") or "")
        if s_date:
            service_lines.append(f"{s_display}: do {s_date.strftime('%d. %m. %Y')}")

    if len(active_products) == 1 or not service_lines:
        suggested = f"Viazanosť na Vami zadanom čísle je do: {latest_to.strftime('%d. %m. %Y')}."
    else:
        suggested = "Vaše aktívne viazanosti:\n" + "\n".join(f"• {ln}" for ln in service_lines)

    if days_left < 90:
        return "Prolongacne_okno", suggested, latest_to
    if days_left < 365:
        return "Viazanost_do_roka", suggested, latest_to
    return "Viazanost_viac_ako_rok", suggested, latest_to


def register(
    registry: ToolRegistry,
    *,
    client: DPSGetClient,
    max_candidates: int = 10,
) -> None:
    """Register identity tools onto the FastMCP registry."""

    async def _identify_and_respond(
        identification_id: str,
        identification_type: str,
        not_found_message: str,
        conversation_id: str,
        log_id_tag: str,
        method: str,
    ) -> str:
        """Shared identification flow used by all identification tools.

        ``identification_id`` must already be validated by the caller.
        """
        _log.info(
            "identification called %s=%s max_candidates=%s",
            log_id_tag,
            identification_id[-4:],
            max_candidates,
        )

        try:
            parties_raw = await client.get_parties_by_identification(
                identification_id, identification_type
            )
        except DPSError as exc:
            _log.warning("party lookup failed (%s): %s", identification_type, exc)
            return _json(_dps_error_payload(exc))

        # Filter to Party records; accept initialized, validated, null/missing.
        # Reject only terminal states: deceased, closed.
        seen: set[str] = set()
        parties: list[dict[str, Any]] = []
        for p in parties_raw:
            if p.get("entityType") != "Party":
                continue
            if p.get("status") in ("deceased", "closed"):
                continue  # accept initialized, validated, null/missing
            pid = p.get("id")
            if not isinstance(pid, str) or pid in seen:
                continue
            seen.add(pid)
            parties.append(p)

        if not parties:
            return _json(
                {
                    "found": False,
                    "error": "not_found",
                    "message": not_found_message,
                }
            )

        capped = parties[:max_candidates]

        # Step B fanout, concurrently per Party.
        try:
            customer_lists = await asyncio.gather(
                *(client.get_customers_by_engaged_party(p["id"]) for p in capped)
            )
        except DPSError as exc:
            _log.warning("customer fanout failed (%s): %s", identification_type, exc)
            return _json(_dps_error_payload(exc))

        candidates: list[dict[str, Any]] = []
        for party, customers in zip(capped, customer_lists, strict=True):
            if customers:
                candidates.extend(_candidate(party, c) for c in customers)
            else:
                candidates.append(_candidate(party, None))

        # Count unique party_ids to determine single vs. multi-match.
        unique_party_ids = {c["party_id"] for c in candidates}

        # Cache full candidates for downstream tools and push NLP state update.
        _persist_identification(
            conversation_id=conversation_id,
            method=method,
            value=identification_id,
            candidates=candidates,
        )

        if len(unique_party_ids) == 1:
            # Single match — return just the name.
            name = candidates[0]["name"]
            return _json({"found": True, "name": name})

        # Multiple matches — return deduplicated sorted names.
        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": "Pre toto identifikačné číslo som našla viacero záznamov. Bude potrebné si vyžiadať dodatočné údaje.",
            }
        )

    # --- Internal identifier lookups -------------------------------------
    # Each assumes the value is already validated/normalised by the dispatcher.
    # NLP load/flush is handled once by the `identifikacia` dispatcher, not here.

    async def _lookup_rodne_cislo(value: str, conv: str) -> str:
        return await _identify_and_respond(
            identification_id=value,
            identification_type="socialSecurityNumber",
            not_found_message=_RC_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="rc_last4",
            method="rodne_cislo",
        )

    async def _lookup_ico(value: str, conv: str) -> str:
        return await _identify_and_respond(
            identification_id=value,
            identification_type="subjectRegistrationId",
            not_found_message=_ICO_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="ico_last4",
            method="ico",
        )

    async def _lookup_kod_zakaznika(value: str, conv: str) -> str:
        _log.info("identifikacia kod_zakaznika code_suffix=%s conv=%s", value[-1], conv)
        try:
            if value.endswith("0"):
                customer = await client.get_customer_by_id(value)
            else:
                ba = await client.get_billing_account_by_id(value)
                if ba is None:
                    customer = None
                else:
                    cust_id = (ba.get("customer") or {}).get("id")
                    if not isinstance(cust_id, str):
                        _log.warning(
                            "billing account %s has no customer.id — treating as not_found", value
                        )
                        customer = None
                    else:
                        customer = await client.get_customer_by_id(cust_id)
        except DPSError as exc:
            _log.warning("identifikacia kod_zakaznika failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if customer is None:
            return _json({"found": False, "error": "not_found", "message": _KOD_NOT_FOUND_MESSAGE})

        candidate = _candidate_from_customer(customer)
        if not candidate.get("name"):
            # Defensive: the Customer record exists but has no usable name field
            return _json({"found": False, "error": "not_found", "message": _KOD_NOT_FOUND_MESSAGE})

        _persist_identification(
            conversation_id=conv,
            method="kod_zakaznika",
            value=value,
            candidates=[candidate],
        )

        return _json({"found": True, "name": candidate["name"]})

    async def _lookup_telefon(normalized: str, conv: str) -> str:
        _log.info("identifikacia telefon msisdn_last4=%s conv=%s", normalized[-4:], conv)

        try:
            products = await client.get_products_by_public_identifier(normalized)
        except DPSError as exc:
            _log.warning("identifikacia telefon product lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if not products:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # Extract unique customer ids from returned products
        customer_ids: list[str] = []
        seen: set[str] = set()
        for p in products:
            cid = (p.get("customer") or {}).get("id")
            if isinstance(cid, str) and cid not in seen:
                seen.add(cid)
                customer_ids.append(cid)

        if not customer_ids:
            _log.warning("identifikacia telefon: products returned but no customer.id linkage")
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # Fetch each unique customer
        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia telefon customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))

        customers = [c for c in customers_raw if c is not None]
        if not customers:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        candidates = [_candidate_from_customer(c) for c in customers]
        candidates = [c for c in candidates if c.get("name")]
        if not candidates:
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # The queried MSISDN matched the customer's Product.publicIdentifier — that is a
        # verified mobile contact for this customer. Inject it into candidate.contacts so
        # the auth tool's `trusted_source` factor can match against it without an extra
        # Party fetch.
        for cand in candidates:
            existing = list(cand.get("contacts") or [])
            if not any(
                ct.get("type") == "mobile"
                and _normalize_msisdn(ct.get("value") or "") == normalized
                for ct in existing
            ):
                existing.append({"type": "mobile", "value": normalized})
                cand["contacts"] = existing

        _persist_identification(
            conversation_id=conv,
            method="telefon",
            value=normalized,
            candidates=candidates,
        )

        if len(candidates) == 1:
            return _json({"found": True, "name": candidates[0]["name"]})

        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": (
                    "Pre toto telefónne číslo som našla viacero záznamov. "
                    "Bude potrebné si vyžiadať dodatočné údaje."
                ),
            }
        )

    async def _lookup_seriove_cislo(normalized: str, conv: str) -> str:
        _log.info(
            "identifikacia seriove_cislo serial_last4=%s conv=%s", normalized[-4:], conv
        )

        try:
            products = await client.get_products_by_serial_number(normalized)
        except DPSError as exc:
            _log.warning("identifikacia seriove_cislo product lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        if not products:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        # Extract unique customer ids
        customer_ids: list[str] = []
        seen: set[str] = set()
        for p in products:
            cid = (p.get("customer") or {}).get("id")
            if isinstance(cid, str) and cid not in seen:
                seen.add(cid)
                customer_ids.append(cid)

        if not customer_ids:
            _log.warning(
                "identifikacia seriove_cislo: products returned but no customer.id linkage"
            )
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia seriove_cislo customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))

        customers = [c for c in customers_raw if c is not None]
        if not customers:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        candidates = [_candidate_from_customer(c) for c in customers]
        candidates = [c for c in candidates if c.get("name")]
        if not candidates:
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        _persist_identification(
            conversation_id=conv,
            method="seriove_cislo",
            value=normalized,
            candidates=candidates,
        )

        if len(candidates) == 1:
            return _json({"found": True, "name": candidates[0]["name"]})

        names = sorted({c["name"] for c in candidates if c["name"]})
        return _json(
            {
                "found": True,
                "multiple_matches": True,
                "names": names,
                "message": (
                    "Pre toto sériové číslo som našla viacero záznamov. "
                    "Bude potrebné si vyžiadať dodatočné údaje."
                ),
            }
        )

    # --- Dispatcher: the single public identification tool ----------------

    async def _route_identifikacia(typ: str, hodnota: str, conv: str) -> str:
        """Validate/normalise per type then call the matching internal lookup."""
        if typ == _TYPE_TELEFON:
            normalized = _normalize_msisdn(hodnota)
            if not normalized:
                return _json({"found": False, "error": "invalid_input", "message": _TEL_INVALID_MESSAGE})
            return await _lookup_telefon(normalized, conv)
        if typ == _TYPE_ICO:
            value = hodnota.strip()
            if not _ICO_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _ICO_INVALID_MESSAGE})
            return await _lookup_ico(value, conv)
        if typ == _TYPE_RODNE_CISLO:
            value = hodnota.strip()
            if not _RC_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _RC_INVALID_MESSAGE})
            return await _lookup_rodne_cislo(value, conv)
        if typ == _TYPE_KOD_ZAKAZNIKA:
            value = hodnota.strip()
            if not _KOD_PATTERN.fullmatch(value):
                return _json({"found": False, "error": "invalid_input", "message": _KOD_INVALID_MESSAGE})
            return await _lookup_kod_zakaznika(value, conv)
        if typ == _TYPE_SERIOVE_CISLO:
            normalized = _normalize_serial(hodnota)
            if not normalized:
                return _json({"found": False, "error": "invalid_input", "message": _SERIAL_INVALID_MESSAGE})
            return await _lookup_seriove_cislo(normalized, conv)
        return _json({"found": False, "error": "invalid_input", "message": _IDENT_UNRECOGNIZED_MESSAGE})

    @mcp_tool(name="identifikacia", description=_IDENTIFIKACIA_TOOL_DESCRIPTION, registry=registry)
    async def identifikacia(
        hodnota: Annotated[
            str | None,
            Field(
                description=(
                    "Identifikačný údaj zákazníka (telefón, IČO, rodné číslo, kód zákazníka, "
                    "sériové číslo). Nechaj prázdne v chat kanáli — zobrazí sa widget a hodnotu "
                    "si tool prečíta z pamäte konverzácie."
                )
            ),
        ] = None,
        typ: Annotated[
            str | None,
            Field(
                description=(
                    "Voliteľný typ údaja: telefon | ico | rodne_cislo | kod_zakaznika | "
                    "seriove_cislo | auto. Default 'auto' — typ sa rozpozná automaticky podľa formátu."
                )
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        try:
            nlp = _nlp_get_named_entities(conv)
            channel = (nlp.get(_CHANNEL_KEY) or "").strip().lower()
            hodnota = (hodnota or nlp.get(widgets.IDENT_INPUT_KEY) or "").strip()
            typ = (typ or nlp.get(widgets.IDENT_TYPE_KEY) or "").strip().lower()

            # No value and no explicit type → this tool does NOT render a widget.
            # Tell the LLM to show the form (chat) or ask for the value (non-chat).
            # (An explicit `typ` with an empty value falls through to per-type
            # validation below, which returns invalid_input.)
            if not hodnota and typ in ("", _TYPE_AUTO):
                instruction = (
                    "V chat kanáli zobraz formulár cez zobraz_identifikacny_widget a počkaj na "
                    "odpoveď zákazníka."
                    if channel == _CHANNEL_CHAT
                    else "Zisti od zákazníka identifikačný údaj a zavolaj identifikacia(hodnota=<údaj>)."
                )
                return _json(
                    {
                        "found": False,
                        "error": "input_required",
                        "suggested_response": _IDENT_INPUT_REQUIRED_MESSAGE,
                        "instruction": instruction,
                    }
                )

            # Resolve the type — explicit dropdown/param value wins over auto-detection.
            if typ in ("", _TYPE_AUTO):
                detected, alternatives = _classify_identifier(hodnota)
                if detected is None:
                    if alternatives and channel == _CHANNEL_CHAT:
                        # Genuine collision → ask the customer to pick the type via the widget.
                        return _json(
                            {
                                "found": False,
                                "error": "ambiguous_type",
                                "alternatives": alternatives,
                                "suggested_response": _IDENT_AMBIGUOUS_MESSAGE,
                                "instruction": (
                                    "Zobraz zobraz_identifikacny_widget(s_vyberom_typu=True), nech "
                                    "zákazník vyberie typ údaja, a počkaj na odoslanie."
                                ),
                            }
                        )
                    return _json(
                        {
                            "found": False,
                            "error": "invalid_input",
                            "message": _IDENT_UNRECOGNIZED_MESSAGE,
                        }
                    )
                typ = detected

            return await _route_identifikacia(typ, hodnota, conv)
        finally:
            _nlp_flush(conv)

    @mcp_tool(
        name="zobraz_identifikacny_widget",
        description=_IDENT_WIDGET_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def zobraz_identifikacny_widget(
        s_vyberom_typu: Annotated[  # noqa: FBT002
            bool,
            Field(
                description="True iba keď identifikacia vrátila error=ambiguous_type — pridá do "
                "formulára výber typu údaja. Inak nechaj False (jednoduchý formulár)."
            ),
        ] = False,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        channel = (_nlp_get_named_entities(conv).get(_CHANNEL_KEY) or "").strip().lower()
        if channel != _CHANNEL_CHAT:
            return _json(
                {
                    "rendered": False,
                    "reason": "not_chat",
                    "suggested_response": _IDENT_INPUT_REQUIRED_MESSAGE,
                    "instruction": (
                        "Toto nie je chat kanál — widget sa nezobrazí. Vypýtaj údaj textom "
                        "a zavolaj identifikacia(hodnota=<údaj>)."
                    ),
                }
            )
        caption = (
            "Zadaný údaj sa dal rozpoznať viacerými spôsobmi — vyberte, prosím, jeho typ."
            if s_vyberom_typu
            else None
        )
        return _json(
            bubble_widget_result(
                summary="Identifikačný widget",
                widget=widgets.identifikacia_widget(caption=caption, with_type_select=s_vyberom_typu),
                template="identifikacia",
                assistant_text="Pošlite mi, prosím, identifikačný údaj cez tento formulár.",
            )
        )

    @mcp_tool(
        name="zobraz_autentifikacny_widget",
        description=_AUTH_WIDGET_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def zobraz_autentifikacny_widget(
        faktor: Annotated[
            str | None,
            Field(
                description="Voliteľný faktor: meno_priezvisko | kod_adresata | rc_last4. "
                "Ak vynecháš, určí sa automaticky podľa priebehu overenia."
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        await _nlp_load(conv)
        channel = (_nlp_get_named_entities(conv).get(_CHANNEL_KEY) or "").strip().lower()
        if channel != _CHANNEL_CHAT:
            return _json(
                {
                    "rendered": False,
                    "reason": "not_chat",
                    "instruction": "Mimo chatu zbieraj overovací údaj textom cez autentifikacia(...).",
                }
            )
        # LLM may pass the factor under its widget key (meno_priezvisko) or the
        # internal factor name (name) — accept both.
        requested = (faktor or "").strip()
        factor = {"meno_priezvisko": _FACTOR_NAME}.get(requested, requested)
        if factor not in widgets.AUTH_FIELD_KEYS:
            factor = _peek_next_auth_factor(conv)
        if factor not in widgets.AUTH_FIELD_KEYS:
            return _json(
                {
                    "rendered": False,
                    "reason": "no_factor",
                    "instruction": (
                        "Nemám aktuálny overovací faktor — najprv identifikuj zákazníka a "
                        "vyhodnoť stav cez autentifikacia()."
                    ),
                }
            )
        suggested = _suggested_response_for_factor(factor)
        return _json(
            bubble_widget_result(
                summary=f"Autentifikačný widget ({factor})",
                widget=widgets.auth_factor_widget(factor, caption=suggested),
                template="autentifikacia",
                assistant_text=suggested,
            )
        )

    @mcp_tool(name="autentifikacia", description=_AUTH_TOOL_DESCRIPTION, registry=registry)
    async def autentifikacia(
        meno_priezvisko: Annotated[
            str | None,
            Field(
                description="Meno a priezvisko zákazníka (faktor 2). Neposielaj naraz s inými faktormi."
            ),
        ] = None,
        kod_adresata: Annotated[
            str | None,
            Field(description="Kód adresáta = kód fakturačného účtu z faktúry (faktor 3)."),
        ] = None,
        rc_last4: Annotated[
            str | None,
            Field(description="Posledné 4 cifry rodného čísla (faktor 4)."),
        ] = None,
        skip_current_factor: Annotated[  # noqa: FBT002
            bool,
            Field(
                description="True ak zákazník nemá údaj na aktuálne pýtaný faktor — preskočiť na ďalší."
            ),
        ] = False,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json(
                {
                    "authenticated": False,
                    "error": "missing_conversation_id",
                    "message": "Vyskytol sa technický problém. Prepojím vás na operátora.",
                }
            )

        await _nlp_load(conv)
        try:
            # Step 1: identification must exist
            identity = _IDENTITY_STATE.get(conv)
            if not identity:
                return _json(
                    {
                        "authenticated": False,
                        "error": "identification_required",
                        "suggested_response": (
                            "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                            "kód zákazníka alebo telefónne číslo?"
                        ),
                        "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                    }
                )

            candidates = identity.get("candidates") or []
            if not candidates:
                return _json(
                    {
                        "authenticated": False,
                        "error": "identification_required",
                        "suggested_response": (
                            "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                            "kód zákazníka alebo telefónne číslo?"
                        ),
                        "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                    }
                )

            # If multi-match identification, can't authenticate against ambiguous candidate
            if len(candidates) > 1:
                return _json(
                    {
                        "authenticated": False,
                        "error": "ambiguous_identification",
                        "suggested_response": (
                            "Z identifikácie máme viacero záznamov, potrebujeme jednoznačnú identifikáciu pred overením."
                        ),
                        "instruction": "Zaveď zákazníka cez presnejší identifikačný údaj.",
                    }
                )

            candidate = candidates[0]
            identification_method = identity.get("identification_method") or ""
            identification_value = identity.get("identification_value") or ""

            # Step 2: load or init auth state
            state = _AUTH_STATE.get(conv) or {
                "factors_satisfied": set(),
                "factors_failed": set(),
                "factors_skipped": set(),
                "factors_attempts": {},
                "authenticated_standard": False,
                "authenticated_sensitive": False,
            }
            # Convert lists from cached dict back to sets (TTLStore stores whatever we put in)
            for key in ("factors_satisfied", "factors_failed", "factors_skipped"):
                if isinstance(state.get(key), list):
                    state[key] = set(state[key])

            # Step 3: read NLP mirror
            nlp = _nlp_get_named_entities(conv)
            input_source = nlp.get("input_source", "")
            channel = (nlp.get(_CHANNEL_KEY) or "").strip().lower()
            auth_type = nlp.get("authentication_type") or _AUTH_TYPE_STANDARD
            if auth_type not in (_AUTH_TYPE_STANDARD, _AUTH_TYPE_SENSITIVE):
                auth_type = _AUTH_TYPE_STANDARD

            # Chat widget submit path: a factor value entered in the auth widget lands
            # in named_entities. Pull it (consume-once) when the LLM didn't pass a param.
            if meno_priezvisko is None:
                meno_priezvisko = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["name"])
            if kod_adresata is None:
                kod_adresata = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["kod_adresata"])
            if rc_last4 is None:
                rc_last4 = _consume_named_entity(conv, widgets.AUTH_FIELD_KEYS["rc_last4"])

            # Step 4: auto-credit factors (recompute every call — input_source may arrive late)
            credited = _credited_factors_from_identification(
                identification_method,
                identification_value,
                candidate.get("contacts") or [],
                input_source,
            )
            state["factors_satisfied"] |= credited

            blocked = state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]

            # Auto-skip trusted_source if input_source is missing (nothing the caller can do).
            # This must happen before out-of-order checks so that factor 2 is the first "expected".
            if (
                _FACTOR_TRUSTED_SOURCE not in state["factors_satisfied"]
                and _FACTOR_TRUSTED_SOURCE not in blocked
                and not input_source
            ):
                state["factors_skipped"].add(_FACTOR_TRUSTED_SOURCE)
                blocked = (
                    state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]
                )

            expected = _next_factor(blocked)

            # Step 5: process user-provided input (one factor per call)
            provided: list[tuple[str, str]] = []
            if meno_priezvisko is not None:
                provided.append((_FACTOR_NAME, meno_priezvisko))
            if kod_adresata is not None:
                provided.append((_FACTOR_KOD_ADRESATA, kod_adresata))
            if rc_last4 is not None:
                provided.append((_FACTOR_RC_LAST4, rc_last4))

            if len(provided) > 1:
                return _json(
                    {
                        "authenticated": False,
                        "error": "multiple_factors_in_call",
                        "suggested_response": "Vyskytol sa technický problém. Prepojím vás na operátora.",
                        "instruction": "Pošli vždy len jeden faktor naraz.",
                    }
                )

            if provided:
                factor, value = provided[0]
                if expected != factor:
                    return _json(
                        {
                            "authenticated": False,
                            "error": "out_of_order",
                            "expected_factor": expected,
                            "suggested_response": _suggested_response_for_factor(expected)
                            if expected
                            else "Pre opätovné vyhodnotenie zavolaj autentifikacia.",
                            "instruction": _instruction_for_factor(expected)
                            if expected
                            else "Zavolaj autentifikacia bez parametrov.",
                        }
                    )
                # Verify
                ok = False
                if factor == _FACTOR_NAME:
                    ok = _check_name(value, candidate.get("name"))
                elif factor == _FACTOR_KOD_ADRESATA:
                    ok = _check_kod_adresata(value, candidate.get("billing_account_ids") or [])
                elif factor == _FACTOR_RC_LAST4:
                    rc4 = candidate.get("auth_rc_last4")
                    if rc4 is None:
                        # Lazy-fetch Party to get RČ last4 (identified via customer-only path)
                        party_id = candidate.get("party_id")
                        if isinstance(party_id, str) and party_id:
                            try:
                                party = await client.get_party_by_id(party_id)
                            except DPSError as exc:
                                _log.warning("autentifikacia: party fetch failed for rc check: %s", exc)
                                return _json(_dps_error_payload(exc))
                            if party is not None:
                                rc4 = _extract_rc_last4_from_party(party)
                                if rc4:
                                    candidate["auth_rc_last4"] = rc4
                                    # Write back the enriched candidate to identity cache
                                    _IDENTITY_STATE.set(conv, identity)
                    ok = _check_rc_last4(value, rc4)

                attempts_map = state["factors_attempts"]
                if ok:
                    state["factors_satisfied"].add(factor)
                    attempts_map.pop(factor, None)
                else:
                    attempts_map[factor] = attempts_map.get(factor, 0) + 1
                    if attempts_map[factor] >= _MAX_AUTH_ATTEMPTS_PER_FACTOR:
                        state["factors_failed"].add(factor)
                        _persist_auth_state(conv, state)
                        _log.warning(
                            "auth factor=%s failed after %d attempts conv=%s",
                            factor,
                            attempts_map[factor],
                            conv,
                        )
                        # Fall through to recompute next step
                    else:
                        remaining = _MAX_AUTH_ATTEMPTS_PER_FACTOR - attempts_map[factor]
                        _persist_auth_state(conv, state)
                        retry_msg = (
                            f"Tento údaj sa nezhoduje. Skúste, prosím, znova. "
                            f"Zostáva vám {'ešte jeden pokus' if remaining == 1 else f'{remaining} pokusy'}."
                        )
                        retry_instruction = (
                            "V chat kanáli zobraz znova zobraz_autentifikacny_widget a počkaj na odoslanie."
                            if channel == _CHANNEL_CHAT and factor in widgets.AUTH_FIELD_KEYS
                            else _instruction_for_factor(factor)
                        )
                        return _json(
                            {
                                "authenticated": False,
                                "factor_failed": factor,
                                "attempts_remaining": remaining,
                                "suggested_response": retry_msg,
                                "instruction": retry_instruction,
                            }
                        )

            # Step 6: skip current factor if requested
            if skip_current_factor and expected and expected != _FACTOR_TRUSTED_SOURCE:
                state["factors_skipped"].add(expected)

            blocked = state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]

            # Step 7: check if we have enough
            required = _required_factors(auth_type)
            satisfied_count = len(state["factors_satisfied"])

            if satisfied_count >= required:
                state["authenticated_standard"] = True
                if satisfied_count >= 3 or auth_type == _AUTH_TYPE_SENSITIVE:
                    state["authenticated_sensitive"] = satisfied_count >= 3
                _persist_auth_state(conv, state)
                level = (
                    _AUTH_TYPE_SENSITIVE if state["authenticated_sensitive"] else _AUTH_TYPE_STANDARD
                )
                _nlp_set_state(
                    conv,
                    {
                        "authenticated_standard": "true",
                        "authenticated_sensitive": "true"
                        if state["authenticated_sensitive"]
                        else "false",
                        "authentication_level": level,
                    },
                )
                return _json(
                    {
                        "authenticated": True,
                        "level": level,
                        "factors_satisfied": sorted(state["factors_satisfied"]),
                        "suggested_response": "Ďakujem, overenie prebehlo úspešne. S čím vám môžem pomôcť?",
                    }
                )

            # Step 8: find next factor
            next_f = _next_factor(blocked)
            _persist_auth_state(conv, state)

            if next_f is None:
                return _json(
                    {
                        "authenticated": False,
                        "error": "cannot_authenticate",
                        "factors_satisfied": sorted(state["factors_satisfied"]),
                        "factors_failed": sorted(state["factors_failed"]),
                        "factors_skipped": sorted(state["factors_skipped"]),
                        "suggested_response": "Overenie sa nepodarilo. Prepájam vás na operátora.",
                        "instruction": "Eskaluj na ľudského operátora.",
                    }
                )

            if next_f == _FACTOR_TRUSTED_SOURCE:
                # Trusted source is automatic — nothing the caller can do.
                # Mark it as skipped (input_source missing or didn't match) and re-eval.
                state["factors_skipped"].add(_FACTOR_TRUSTED_SOURCE)
                _persist_auth_state(conv, state)
                next_f = _next_factor(
                    state["factors_satisfied"] | state["factors_failed"] | state["factors_skipped"]
                )
                if next_f is None:
                    return _json(
                        {
                            "authenticated": False,
                            "error": "cannot_authenticate",
                            "suggested_response": "Overenie sa nepodarilo. Prepájam vás na operátora.",
                            "instruction": "Eskaluj na ľudského operátora.",
                        }
                    )

            return _need_factor_response(
                next_f,
                auth_type=auth_type,
                satisfied=state["factors_satisfied"],
                required=required,
                channel=channel,
            )
        finally:
            _nlp_flush(conv)

    @mcp_tool(name="nastav_test_kontext", description=_TEST_KONTEXT_DESCRIPTION, registry=registry)
    async def nastav_test_kontext(
        input_source: Annotated[
            str | None,
            Field(
                description="Telefónne číslo alebo email volajúceho — simuluje caller-ID / from-header z NLP."
            ),
        ] = None,
        authentication_type: Annotated[
            str | None,
            Field(description="'standard' alebo 'sensitive' — simuluje typ transakcie z NLP."),
        ] = None,
        channel: Annotated[
            str | None,
            Field(
                description="Kanál konverzácie, napr. 'chat' (zapne widgety) alebo 'voice' — "
                "simuluje named_entity Channel z NLP."
            ),
        ] = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({"ok": False, "error": "missing_conversation_id"})

        await _nlp_load(conv)
        # Debug tool: write straight to the read mirror (simulate NLP-provided
        # named_entities). These are NOT queued for push back to the NLP engine.
        entities: dict[str, str] = {}
        if input_source is not None:
            entities["input_source"] = input_source
        if authentication_type is not None:
            entities["authentication_type"] = authentication_type
        if channel is not None:
            entities[_CHANNEL_KEY] = channel
        if entities:
            current = _NLP_MIRROR_STATE.get(conv) or {}
            current.update(entities)
            _NLP_MIRROR_STATE.set(conv, current)
        return _json({"ok": True, "named_entities": dict(_NLP_MIRROR_STATE.get(conv) or {})})

    @mcp_tool(name="over_viazanost", description=_VIAZANOST_TOOL_DESCRIPTION, registry=registry)
    async def over_viazanost(_meta: dict[str, Any] | None = None) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({
                "found": False,
                "error": "missing_conversation_id",
                "message": _UPSTREAM_ERROR_MESSAGE,
            })

        await _nlp_load(conv)
        try:
            # Require standard authentication before returning contract data.
            auth_state = _AUTH_STATE.get(conv)
            if not auth_state or not auth_state.get("authenticated_standard"):
                return _json({
                    "found": False,
                    "error": "authentication_required",
                    "suggested_response": (
                        "Na zobrazenie informácií o viazanosti musím najprv overiť vašu totožnosť."
                    ),
                    "instruction": "Zavolaj autentifikacia tool a až potom over_viazanost.",
                })

            identity = _IDENTITY_STATE.get(conv)
            if not identity:
                return _json({
                    "found": False,
                    "error": "identification_required",
                    "suggested_response": (
                        "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                        "kód zákazníka alebo telefónne číslo?"
                    ),
                    "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                })

            candidates = identity.get("candidates") or []
            if not candidates:
                return _json({
                    "found": False,
                    "error": "identification_required",
                    "suggested_response": (
                        "Najprv potrebujem zistiť, kto ste. Môžete mi povedať vaše rodné číslo, "
                        "kód zákazníka alebo telefónne číslo?"
                    ),
                    "instruction": "Zavolaj najprv niektorý z identifikacia_* toolov.",
                })

            candidate = candidates[0]
            customer_id: str | None = candidate.get("customer_id") or None
            identification_method = identity.get("identification_method") or ""
            identification_value = identity.get("identification_value") or ""
            # Prefer customer_id (covers all products); fall back to MSISDN only when
            # identification was by phone and no customer_id linkage exists.
            msisdn: str | None = (
                identification_value if (not customer_id and identification_method == "telefon") else None
            )

            _log.info(
                "over_viazanost: conv=%s customer_id_last4=%s method=%s",
                conv,
                (customer_id or "")[-4:] or "none",
                identification_method,
            )

            try:
                products_raw = await client.get_products_for_agreements(
                    customer_id=customer_id,
                    msisdn=msisdn,
                )
            except DPSError as exc:
                _log.warning("over_viazanost: PI fetch failed: %s", exc)
                return _json(_dps_error_payload(exc))

            today = date.today()
            active_products = _parse_products_with_active_agreements(products_raw, today)
            viazanost_typ, suggested_response, latest_date = _classify_viazanost(active_products, today)

            _log.info(
                "over_viazanost: conv=%s fetched=%d active=%d typ=%s",
                conv,
                len(products_raw),
                len(active_products),
                viazanost_typ,
            )

            # Sort ascending by viazanost_do (soonest commitment ends first)
            active_products.sort(key=lambda p: p.get("viazanost_do") or "")

            # Per-product typ classification
            def _product_typ(viazanost_do_str: str | None) -> str:
                d = _parse_agreement_date(viazanost_do_str or "")
                if d is None:
                    return "Chyba"
                days = (d - today).days
                if days < 90:
                    return "Prolongacne_okno"
                if days < 365:
                    return "Viazanost_do_roka"
                return "Viazanost_viac_ako_rok"

            # Group by typ, preserve _VIAZANOST_TYP_ORDER
            grouped: dict[str, list[dict[str, Any]]] = {}
            for p in active_products:
                typ = _product_typ(p.get("viazanost_do"))
                grouped.setdefault(typ, []).append(p)
            services_grouped = {k: grouped[k] for k in _VIAZANOST_TYP_ORDER if k in grouped}

            _nlp_set_state(conv, {"over_viazanost_result": viazanost_typ})
            return _json({
                "found": True,
                "viazanost_typ": viazanost_typ,
                "viazanost_do": str(latest_date) if latest_date else None,
                "services": services_grouped,
                "count": len(active_products),
                "suggested_response": suggested_response,
            })
        finally:
            _nlp_flush(conv)
