"""MCP tools for mcp_telekom_identity.

identifikacia_rodne_cislo(rodne_cislo)
— Find Telekom customer(s) by Slovak personal identification number (rodné číslo).

identifikacia_op(cislo_op)
— Find Telekom customer(s) by Slovak national ID card number (občiansky preukaz).

identifikacia_pas(cislo_pasu)
— Find Telekom customer(s) by passport number.

identifikacia_ico(ico)
— Find Telekom customer(s) by Slovak company ID (IČO).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
from lib.mcp_service.state import TTLStore
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

# SK OP since 1993: exactly 2 uppercase letters + 6 digits (8 chars). Pre-1993
# Czechoslovak numeric IDs are expired and not in active DPS records.
# Strip whitespace/dashes + uppercase before matching so "EA 123 456" and "ea-123456" normalize.
_OP_NORMALIZE_RE = re.compile(r"[\s\-]")

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


_OP_PATTERN = re.compile(r"^[A-Z]{2}\d{6}$")
_OP_INVALID_MESSAGE = "Číslo občianskeho preukazu má tvar 2 písmená a 6 cifier (napr. AB123456)."
_OP_NOT_FOUND_MESSAGE = "Zákazníka s týmto číslom občianskeho preukazu sa nepodarilo nájsť."
_OP_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa čísla občianskeho preukazu. Po úspechu vráti meno "
    "(alebo zoznam mien ak je záznamov viac). Interné identifikátory a kontakty "
    "si tool uloží do pamäte konverzácie pre ďalšie nástroje — netreba ich od neho "
    "znova žiadať."
)

# Passport format: 2 letters + 6-8 digits (SK) or 1-2 letters + 6-8 digits (international).
# Strip + uppercase before matching.
_PAS_PATTERN = re.compile(r"^[A-Z]{1,2}\d{6,8}$")
_PAS_INVALID_MESSAGE = (
    "Číslo cestovného pasu nie je v správnom tvare. "
    "Zadajte ho ako 1 alebo 2 písmená a 6 až 8 cifier (napr. BR154151)."
)
_PAS_NOT_FOUND_MESSAGE = "Zákazníka s týmto číslom cestovného pasu sa nepodarilo nájsť."
_PAS_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka podľa čísla cestovného pasu. Po úspechu vráti meno "
    "(alebo zoznam mien ak je záznamov viac). Interné identifikátory a kontakty "
    "si tool uloží do pamäte konverzácie pre ďalšie nástroje — netreba ich od neho "
    "znova žiadať."
)

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


def _nlp_set_state(conversation_id: str, named_entities: dict[str, Any]) -> None:
    """Mirror locally + fire-and-forget PUT to the NLP engine state endpoint.

    Daemon thread + 1-second timeout. Errors logged at WARNING, never raised.
    """
    if not conversation_id:
        return

    # Mirror locally so we can read these entries back without a real NLP GET.
    current = _NLP_MIRROR_STATE.get(conversation_id) or {}
    # Coerce to str for downstream consumers; named_entities only carries scalars.
    current.update({k: str(v) for k, v in named_entities.items()})
    _NLP_MIRROR_STATE.set(conversation_id, current)

    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/states"
    payload = {"named_entities": named_entities}
    body = json.dumps(payload, ensure_ascii=False).encode()

    def _do() -> None:
        req = urllib.request.Request(
            url, data=body, method="PUT", headers={"Content-Type": "application/json"}
        )
        _log.info("NLP state PUT %s body=%s", url, json.dumps(payload, ensure_ascii=False))
        try:
            with urllib.request.urlopen(req, timeout=_NLP_TIMEOUT_SECONDS) as resp:
                _log.info("NLP state PUT %s -> HTTP %s", url, resp.status)
        except urllib.error.HTTPError as exc:
            _log.warning("NLP state PUT %s -> HTTP %s", url, exc.code)
        except Exception as exc:
            _log.warning("NLP state PUT %s -> error: %s", url, exc)

    threading.Thread(target=_do, daemon=True, name="nlp-state-put-identity").start()


def _nlp_get_named_entities(conversation_id: str) -> dict[str, str]:
    """Read mirrored NLP named_entities for the conversation."""
    if not conversation_id:
        return {}
    return _NLP_MIRROR_STATE.get(conversation_id) or {}


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

_IDENTITY_TTL_SECONDS = 30 * 60
_IDENTITY_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=_IDENTITY_TTL_SECONDS)

# Mirror of named_entities we PUT to NLP, plus externally-set entities like
# input_source and authentication_type. In production we will (Q3 in
# docs/OPEN_QUESTIONS.md) replace this read path with a GET /named_entities
# call against the real NLP engine. For now it lets tests and the
# `nastav_test_kontext` debug tool simulate that state without a real NLP.
_NLP_MIRROR_TTL_SECONDS = 30 * 60
_NLP_MIRROR_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)

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

# Tools whose input is PII (sent to NLP only as last4=XXXX marker).
_PII_METHODS = frozenset({"rodne_cislo", "op", "pas"})


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

    async def identifikacia_rodne_cislo(
        rodne_cislo: Annotated[
            str,
            Field(description="Rodné číslo zákazníka — 9 alebo 10 cifier, bez lomky."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = (rodne_cislo or "").strip()
        if not _RC_PATTERN.fullmatch(value):
            return _json({"found": False, "error": "invalid_input", "message": _RC_INVALID_MESSAGE})
        conv = (_meta or {}).get("conversation_id", "")
        return await _identify_and_respond(
            identification_id=value,
            identification_type="socialSecurityNumber",
            not_found_message=_RC_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="rc_last4",
            method="rodne_cislo",
        )

    async def identifikacia_op(
        cislo_op: Annotated[
            str,
            Field(
                description="Číslo občianskeho preukazu — 2 písmená a 6 cifier (napr. AB123456)."
            ),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = _OP_NORMALIZE_RE.sub("", cislo_op or "").upper()
        if not _OP_PATTERN.fullmatch(value):
            return _json({"found": False, "error": "invalid_input", "message": _OP_INVALID_MESSAGE})
        conv = (_meta or {}).get("conversation_id", "")
        return await _identify_and_respond(
            identification_id=value,
            identification_type="nationalIdentityCard",
            not_found_message=_OP_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="op_last4",
            method="op",
        )

    async def identifikacia_pas(
        cislo_pasu: Annotated[
            str,
            Field(description="Číslo cestovného pasu — napr. BR154151."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = (cislo_pasu or "").strip().upper()
        if not _PAS_PATTERN.fullmatch(value):
            return _json(
                {"found": False, "error": "invalid_input", "message": _PAS_INVALID_MESSAGE}
            )
        conv = (_meta or {}).get("conversation_id", "")
        return await _identify_and_respond(
            identification_id=value,
            identification_type="passport",
            not_found_message=_PAS_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="pas_last4",
            method="pas",
        )

    @mcp_tool(name="identifikacia_ico", description=_ICO_TOOL_DESCRIPTION, registry=registry)
    async def identifikacia_ico(
        ico: Annotated[
            str,
            Field(description="IČO spoločnosti — 8 cifier."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = (ico or "").strip()
        if not _ICO_PATTERN.fullmatch(value):
            return _json(
                {"found": False, "error": "invalid_input", "message": _ICO_INVALID_MESSAGE}
            )
        conv = (_meta or {}).get("conversation_id", "")
        return await _identify_and_respond(
            identification_id=value,
            identification_type="subjectRegistrationId",
            not_found_message=_ICO_NOT_FOUND_MESSAGE,
            conversation_id=conv,
            log_id_tag="ico_last4",
            method="ico",
        )

    @mcp_tool(
        name="identifikacia_kod_zakaznika", description=_KOD_TOOL_DESCRIPTION, registry=registry
    )
    async def identifikacia_kod_zakaznika(
        kod_zakaznika: Annotated[
            str,
            Field(
                description="Kód zákazníka alebo fakturačného účtu — len cifry (napr. 4482259100)."
            ),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = (kod_zakaznika or "").strip()
        if not _KOD_PATTERN.fullmatch(value):
            return _json(
                {"found": False, "error": "invalid_input", "message": _KOD_INVALID_MESSAGE}
            )

        conv = (_meta or {}).get("conversation_id", "")
        _log.info("identifikacia_kod_zakaznika called code_suffix=%s conv=%s", value[-1], conv)

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
            _log.warning("identifikacia_kod_zakaznika failed: %s", exc)
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

    @mcp_tool(name="identifikacia_telefon", description=_TEL_TOOL_DESCRIPTION, registry=registry)
    async def identifikacia_telefon(
        telefon: Annotated[
            str,
            Field(description="Telefónne číslo — 0904... alebo +421904... / 421904..."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        normalized = _normalize_msisdn(telefon or "")
        if not normalized:
            return _json(
                {"found": False, "error": "invalid_input", "message": _TEL_INVALID_MESSAGE}
            )

        conv = (_meta or {}).get("conversation_id", "")
        _log.info("identifikacia_telefon called msisdn_last4=%s conv=%s", normalized[-4:], conv)

        try:
            products = await client.get_products_by_public_identifier(normalized)
        except DPSError as exc:
            _log.warning("identifikacia_telefon product lookup failed: %s", exc)
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
            _log.warning("identifikacia_telefon: products returned but no customer.id linkage")
            return _json({"found": False, "error": "not_found", "message": _TEL_NOT_FOUND_MESSAGE})

        # Fetch each unique customer
        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia_telefon customer fanout failed: %s", exc)
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

    @mcp_tool(
        name="identifikacia_seriove_cislo", description=_SERIAL_TOOL_DESCRIPTION, registry=registry
    )
    async def identifikacia_seriove_cislo(
        seriove_cislo: Annotated[
            str,
            Field(
                description="Sériové číslo zariadenia — 8 až 30 alfanumerických znakov (napr. M91450EB0603)."
            ),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        normalized = _normalize_serial(seriove_cislo or "")
        if not normalized:
            return _json(
                {"found": False, "error": "invalid_input", "message": _SERIAL_INVALID_MESSAGE}
            )

        conv = (_meta or {}).get("conversation_id", "")
        _log.info(
            "identifikacia_seriove_cislo called serial_last4=%s conv=%s", normalized[-4:], conv
        )

        try:
            products = await client.get_products_by_serial_number(normalized)
        except DPSError as exc:
            _log.warning("identifikacia_seriove_cislo product lookup failed: %s", exc)
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
                "identifikacia_seriove_cislo: products returned but no customer.id linkage"
            )
            return _json(
                {"found": False, "error": "not_found", "message": _SERIAL_NOT_FOUND_MESSAGE}
            )

        try:
            customers_raw = await asyncio.gather(
                *(client.get_customer_by_id(cid) for cid in customer_ids)
            )
        except DPSError as exc:
            _log.warning("identifikacia_seriove_cislo customer fanout failed: %s", exc)
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
        auth_type = nlp.get("authentication_type") or _AUTH_TYPE_STANDARD
        if auth_type not in (_AUTH_TYPE_STANDARD, _AUTH_TYPE_SENSITIVE):
            auth_type = _AUTH_TYPE_STANDARD

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
                    return _json(
                        {
                            "authenticated": False,
                            "factor_failed": factor,
                            "attempts_remaining": remaining,
                            "suggested_response": (
                                f"Tento údaj sa nezhoduje. Skúste, prosím, znova. "
                                f"Zostáva vám {'ešte jeden pokus' if remaining == 1 else f'{remaining} pokusy'}."
                            ),
                            "instruction": _instruction_for_factor(factor),
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

        return _json(
            {
                "authenticated": False,
                "level_required": auth_type,
                "factors_satisfied": sorted(state["factors_satisfied"]),
                "factors_remaining": required - satisfied_count,
                "next_factor": next_f,
                "suggested_response": _suggested_response_for_factor(next_f),
                "instruction": _instruction_for_factor(next_f),
            }
        )

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
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({"ok": False, "error": "missing_conversation_id"})

        current = _NLP_MIRROR_STATE.get(conv) or {}
        if input_source is not None:
            current["input_source"] = input_source
        if authentication_type is not None:
            current["authentication_type"] = authentication_type
        _NLP_MIRROR_STATE.set(conv, current)
        return _json({"ok": True, "named_entities": dict(current)})

    @mcp_tool(name="over_viazanost", description=_VIAZANOST_TOOL_DESCRIPTION, registry=registry)
    async def over_viazanost(_meta: dict[str, Any] | None = None) -> str:
        conv = (_meta or {}).get("conversation_id", "")
        if not conv:
            return _json({
                "found": False,
                "error": "missing_conversation_id",
                "message": _UPSTREAM_ERROR_MESSAGE,
            })

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

        return _json({
            "found": True,
            "viazanost_typ": viazanost_typ,
            "viazanost_do": str(latest_date) if latest_date else None,
            "services": services_grouped,
            "count": len(active_products),
            "suggested_response": suggested_response,
        })
