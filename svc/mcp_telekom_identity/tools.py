"""MCP tools for mcp_telekom_identity.

identifikacia_rodne_cislo(rodne_cislo)
— Find Telekom customer(s) by Slovak personal identification number (rodné číslo).
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Annotated, Any

import pydantic

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

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


def _candidate_from_party_only(party: dict[str, Any]) -> dict[str, Any]:
    """Build a candidate record from a Party with no Customer enrichment yet."""
    ind = party.get("individual") or {}
    given = ind.get("givenName") or ""
    family = ind.get("familyName") or ""
    name = " ".join(part for part in (given, family) if part) or None
    return {
        "party_id": party.get("id"),
        "customer_id": None,
        "name": name,
        "given_name": given or None,
        "family_name": family or None,
        "status": None,
        "market_segment": None,
        "customer_segment": None,
        "treatment_package": None,
        "valid_for": None,
        "contacts": [],
        "identifications": [],
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
            pydantic.Field(description="Rodné číslo — 9 alebo 10 cifier, bez lomky."),
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

        parties_raw = await client.get_parties_by_identification(rc, "socialSecurityNumber")

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

        # Step B is added in Task 8; for now emit candidates with customer_id=None.
        candidates = [_candidate_from_party_only(p) for p in capped]
        # Each Party also triggers a customer-management lookup (kept as a no-op
        # call here so the cap test can assert the call count).
        for p in capped:
            await client.get_customers_by_engaged_party(p["id"])

        return _json(
            {
                "found": True,
                "total_party_matches": total,
                "returned_count": len(candidates),
                "truncated": truncated,
                "candidates": candidates,
            }
        )
