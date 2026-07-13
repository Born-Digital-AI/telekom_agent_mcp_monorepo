"""Roaming tools for mcp_telekom_identity - zone/price/package lookup by country.

These tools are independent of the DPS identification flow; they answer
"what does roaming cost in country X" questions from the bundled snapshot of
https://www.telekom.sk/volania/roaming (see :mod:`.roaming` and
:mod:`.roaming_refresh`). No network calls are made at request time.

Tools (registered under ``roaming_*`` to sit next to the ``identifikacia_*``
and ``znalostna_baza_*`` tools):
  - roaming_info           -> zone, prices and packages for one country,
                              optionally cut to one customer segment
  - roaming_zoznam_krajin  -> country list, optionally filtered by zone
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal

import pydantic
from mcp.types import ToolAnnotations

from svc.mcp_telekom_identity.roaming import RoamingCatalog, country_payload

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_log = logging.getLogger(__name__)

_NOT_FOUND_MESSAGE = (
    "Krajinu sa nepodarilo nájsť. Skúste slovenský alebo anglický názov, "
    "prípadne ISO kód (napr. 'Turecko', 'Turkey', 'TR')."
)
_AMBIGUOUS_MESSAGE = "Zadaniu zodpovedá viac krajín - upresnite, ktorú z nich myslíte."

_ZONE_FILTERS: dict[str, str] = {
    "0": "Zóna 0",
    "1": "Zóna 1",
    "2": "Zóna 2",
    "3": "Zóna 3",
    "4": "Zóna 4",
    "bez_roamingu": "Bez roamingu",
}


def _json(obj: Any) -> str:  # noqa: ANN401 - generic JSON serialiser
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _country_summary(country: dict[str, Any]) -> dict[str, Any]:
    """Compact row used in candidate lists and the country listing."""
    return {
        "nazov": country.get("nazov"),
        "zona": country.get("zona"),
        "iso2": country.get("iso2"),
    }


def register_roaming_tools(*, mcp: FastMCP, catalog: RoamingCatalog | None = None) -> None:
    """Register the roaming tools on the given FastMCP instance.

    ``catalog`` defaults to the bundled snapshot; tests inject a small fixture.
    """
    catalog = catalog or RoamingCatalog.load()
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True)
    _log.info(
        "roaming tools enabled (%s countries, snapshot %s)",
        len(catalog.countries),
        catalog.snapshot_date,
    )

    def _source_fields() -> dict[str, str]:
        return {"zdroj": catalog.source, "aktualne_k": catalog.snapshot_date}

    @mcp.tool(name="roaming_info", annotations=read_only)
    async def roaming_info(
        krajina: Annotated[
            str,
            pydantic.Field(
                description=(
                    "Názov krajiny po slovensky alebo anglicky (napr. 'Turecko', 'Turkey'), "
                    "prípadne ISO kód ('TR', 'TUR'). Diakritika nie je potrebná."
                )
            ),
        ],
        typ_zakaznika: Annotated[
            Literal["pausal", "dobijacia_karta", "bez_zavazkov"] | None,
            pydantic.Field(
                description=(
                    "Voliteľný segment zákazníka: pausal (mobilný/biznis paušál) | "
                    "dobijacia_karta (Easy/Swipe) | bez_zavazkov (program Bez záväzkov). "
                    "Bez udania sa vráti prehľad pre všetky tri segmenty."
                )
            ),
        ] = None,
    ) -> str:
        """Zisti roamingové podmienky pre danú krajinu: zónu, ceny volaní/SMS/dát a dostupné balíčky.

        Ceny a balíčky sa líšia podľa typu zákazníka (paušál / dobíjacia karta /
        Bez záväzkov) - ak je segment známy, uveď ``typ_zakaznika``. V krajinách
        EÚ+ (Zóna 0 a 1, ``eu_regulacia=true``) zákazník čerpá minúty, správy a
        dáta z domáceho balíčka; uvedené ceny sú sadzby po jeho vyčerpaní.
        """
        _log.info("roaming_info called krajina=%r typ_zakaznika=%r", krajina, typ_zakaznika)
        match, alternatives = catalog.find(krajina)
        if match is None and len(alternatives) > 1:
            return _json(
                {
                    "found": False,
                    "error": "ambiguous",
                    "message": _AMBIGUOUS_MESSAGE,
                    "kandidati": [_country_summary(country) for country in alternatives],
                }
            )
        if match is None and len(alternatives) == 1:
            match = alternatives[0]
        if match is None:
            return _json(
                {
                    "found": False,
                    "error": "not_found",
                    "message": _NOT_FOUND_MESSAGE,
                    "navrhy": [_country_summary(country) for country in alternatives],
                }
            )
        return _json(
            {
                "found": True,
                **_source_fields(),
                **country_payload(match, typ_zakaznika),
            }
        )

    @mcp.tool(name="roaming_zoznam_krajin", annotations=read_only)
    async def roaming_zoznam_krajin(
        zona: Annotated[
            Literal["0", "1", "2", "3", "4", "bez_roamingu"] | None,
            pydantic.Field(
                description=(
                    "Voliteľný filter na roamingovú zónu: 0 a 1 = EÚ+ (ceny ako doma), "
                    "2 a 3 = skupina Svet, 4 = satelit/lietadlo/loď, "
                    "bez_roamingu = krajiny bez roamingovej služby."
                )
            ),
        ] = None,
    ) -> str:
        """Vypíš krajiny v roamingovom cenníku Telekomu, voliteľne filtrované podľa zóny."""
        zone_name = _ZONE_FILTERS.get(zona) if zona else None
        countries = [
            _country_summary(country)
            for country in catalog.countries
            if zone_name is None or country.get("zona") == zone_name
        ]
        _log.info("roaming_zoznam_krajin called zona=%r -> %s countries", zona, len(countries))
        return _json(
            {
                **_source_fields(),
                "zona": zone_name,
                "pocet": len(countries),
                "krajiny": countries,
            }
        )
