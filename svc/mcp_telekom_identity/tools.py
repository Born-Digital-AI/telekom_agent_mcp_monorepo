"""MCP tools for mcp_telekom_identity.

identifikacia_rodne_cislo(rodne_cislo)
— Find Telekom customer(s) by Slovak personal identification number (rodné číslo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
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
_TOOL_DESCRIPTION = (
    "Identifikuj zákazníka v systéme DPS podľa rodného čísla.\n"
    "Vstup: rodne_cislo — 9 alebo 10 cifier (bez lomky).\n"
    "Výstup: JSON so zoznamom kandidátov (party_id, customer_id, meno, status, "
    "segment, kontakty). Tool zreťazí volania DPS party-management a customer-management."
)
_log = logging.getLogger(__name__)


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


def _normalize_identifications(individual: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for ident in individual.get("individualIdentifications") or []:
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
    given = ind.get("givenName") or ""
    family = ind.get("familyName") or ""
    name = " ".join(part for part in (given, family) if part) or None
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
        "identifications": _normalize_identifications(ind),
    }


def _dps_error_payload(exc: DPSError) -> dict[str, Any]:
    if isinstance(exc, DPSAuthError):
        return {
            "found": False,
            "error": "auth_failed",
            "message": (
                "Autentifikácia voči systému DPS zlyhala. Skontrolujte konfiguráciu tokenu."
            ),
        }
    if isinstance(exc, DPSTimeoutError):
        return {
            "found": False,
            "error": "upstream_timeout",
            "message": "Systém DPS nestihol odpovedať v limite. Skúste znova.",
        }
    if isinstance(exc, DPSNetworkError):
        return {
            "found": False,
            "error": "upstream_unreachable",
            "message": ("Nedá sa pripojiť k systému DPS. Skontrolujte sieťové pripojenie."),
        }
    if isinstance(exc, (DPSUpstreamError, DPSInvalidResponseError)):
        return {
            "found": False,
            "error": "upstream_error",
            "message": ("Systém DPS momentálne nie je dostupný. Skúste o chvíľu znova."),
        }
    return {
        "found": False,
        "error": "upstream_error",
        "message": ("Systém DPS momentálne nie je dostupný. Skúste o chvíľu znova."),
    }


def register(
    registry: ToolRegistry,
    *,
    client: DPSGetClient,
    max_candidates: int = 10,
) -> None:
    """Register identity tools onto the FastMCP registry."""

    @mcp_tool(
        name="identifikacia_rodne_cislo",
        description=_TOOL_DESCRIPTION,
        registry=registry,
    )
    async def identifikacia_rodne_cislo(
        rodne_cislo: Annotated[
            str,
            Field(description="Rodné číslo — 9 alebo 10 cifier, bez lomky."),
        ],
        _meta: dict[str, Any] | None = None,
    ) -> str:
        rc = (rodne_cislo or "").strip()
        if not _RC_PATTERN.fullmatch(rc):
            return _json(
                {
                    "found": False,
                    "error": "invalid_input",
                    "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky).",
                }
            )

        _log.info(
            "identifikacia_rodne_cislo called rc_last4=%s max_candidates=%s",
            rc[-4:],
            max_candidates,
        )

        try:
            parties_raw = await client.get_parties_by_identification(rc, "socialSecurityNumber")
        except DPSError as exc:
            _log.warning("identifikacia_rodne_cislo party lookup failed: %s", exc)
            return _json(_dps_error_payload(exc))

        # Filter to Party records with status=initialized, dedup by id.
        seen: set[str] = set()
        parties: list[dict[str, Any]] = []
        for p in parties_raw:
            if p.get("entityType") != "Party":
                continue
            if p.get("status") != "initialized":
                continue
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
                    "message": (
                        "Pre zadané rodné číslo nebol nájdený žiadny zákazník v systéme DPS."
                    ),
                }
            )

        total = len(parties)
        capped = parties[:max_candidates]
        truncated = total > max_candidates

        # Step B fanout, concurrently per Party.
        try:
            customer_lists = await asyncio.gather(
                *(client.get_customers_by_engaged_party(p["id"]) for p in capped)
            )
        except DPSError as exc:
            _log.warning("identifikacia_rodne_cislo customer fanout failed: %s", exc)
            return _json(_dps_error_payload(exc))

        candidates: list[dict[str, Any]] = []
        for party, customers in zip(capped, customer_lists, strict=True):
            if customers:
                candidates.extend(_candidate(party, c) for c in customers)
            else:
                candidates.append(_candidate(party, None))

        return _json(
            {
                "found": True,
                "total_party_matches": total,
                "returned_count": len(candidates),
                "truncated": truncated,
                "candidates": candidates,
            }
        )
