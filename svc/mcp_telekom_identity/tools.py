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

# New SK OP: 2 uppercase letters + 6 digits (e.g. "AB123456").
# Old SK OP: 6-9 digits (legacy, still valid for some).
# Be lenient: strip + uppercase before matching.
_OP_PATTERN = re.compile(r"^([A-Z]{2}\d{6}|\d{6,9})$")
_OP_INVALID_MESSAGE = (
    "Číslo občianskeho preukazu nie je v správnom tvare. "
    "Zadajte ho ako 2 písmená a 6 cifier (napr. AB123456) alebo ako 6 až 9 cifier."
)
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
            Field(description="Číslo občianskeho preukazu — napr. AB123456 alebo 6–9 cifier."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        value = (cislo_op or "").strip().upper()
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
