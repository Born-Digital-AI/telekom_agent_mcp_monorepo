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
        # Step A/B implemented in later tasks.
        await client.get_parties_by_identification(rc, "socialSecurityNumber")
        return _json({"found": False, "error": "not_found", "message": "stub"})
