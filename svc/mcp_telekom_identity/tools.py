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
import re
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

_UPSTREAM_ERROR_MESSAGE = "Vyskytol sa technický problém. Prepojím vás na operátora."


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

        # Cache full candidates for downstream tools.
        # rc_last4 key name is kept stable across both tools for schema consistency.
        if conversation_id:
            _IDENTITY_STATE.set(
                conversation_id,
                {
                    "rc_last4": identification_id[-4:],
                    "candidates": candidates,
                },
            )
        else:
            _log.warning(
                "identification (%s): no conversation_id in _meta — cache skipped",
                identification_type,
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

    @mcp_tool(name="identifikacia_rodne_cislo", description=_RC_TOOL_DESCRIPTION, registry=registry)
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
        )

    @mcp_tool(name="identifikacia_op", description=_OP_TOOL_DESCRIPTION, registry=registry)
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
        )

    @mcp_tool(name="identifikacia_pas", description=_PAS_TOOL_DESCRIPTION, registry=registry)
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

        if conv:
            _IDENTITY_STATE.set(
                conv,
                {"rc_last4": value[-4:], "candidates": [candidate]},
            )
        else:
            _log.warning("identifikacia_kod_zakaznika: no conversation_id — cache skipped")

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

        if conv:
            _IDENTITY_STATE.set(
                conv,
                {"rc_last4": normalized[-4:], "candidates": candidates},
            )
        else:
            _log.warning("identifikacia_telefon: no conversation_id — cache skipped")

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

        if conv:
            _IDENTITY_STATE.set(
                conv,
                {"rc_last4": normalized[-4:], "candidates": candidates},
            )
        else:
            _log.warning("identifikacia_seriove_cislo: no conversation_id — cache skipped")

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
