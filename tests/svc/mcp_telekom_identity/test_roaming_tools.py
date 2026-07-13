"""Unit tests for the identity service's roaming tools.

Behavioral tests (matching, segment filtering, error payloads) run against a
small synthetic catalog so they don't break when Telekom changes prices.
Structural tests run against the real bundled snapshot to catch a broken or
truncated refresh (`python -m svc.mcp_telekom_identity.roaming_refresh`).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from svc.mcp_telekom_identity.roaming import (
    SEGMENTS,
    RoamingCatalog,
    country_payload,
    normalize_name,
)
from svc.mcp_telekom_identity.roaming_tools import register_roaming_tools

INFO_TOOL = "roaming_info"
LIST_TOOL = "roaming_zoznam_krajin"

KNOWN_ZONES = {"Zóna 0", "Zóna 1", "Zóna 2", "Zóna 3", "Zóna 4", "Bez roamingu"}


class _FakeMCP:
    """Captures tools registered via `@mcp.tool(name=..., annotations=...)`."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(
        self,
        *,
        name: str | None = None,
        annotations: Any = None,  # noqa: ARG002
        description: str | None = None,  # noqa: ARG002
    ):
        def decorator(fn: Any) -> Any:
            self.registered[name or fn.__name__] = fn
            return fn

        return decorator


def _segment(prices: dict[str, float] | None, packages: list[str]) -> dict[str, Any]:
    return {
        "ceny": prices,
        "info": "info text",
        "balicky": [
            {"nazov": p, "cena_eur": 8.0, "poplatok": "Jednorazovo", "popis": ""} for p in packages
        ],
        "poznamky": [],
    }


def _country(
    name: str,
    zone: str,
    *,
    aliases: list[str] | None = None,
    iso2: str | None = None,
    iso3: str | None = None,
) -> dict[str, Any]:
    prices = None if zone == "Bez roamingu" else {"sms_eur": 0.4}
    return {
        "nazov": name,
        "slug": normalize_name(name).replace(" ", "-"),
        "aliasy": aliases or [],
        "iso2": iso2,
        "iso3": iso3,
        "zona": zone,
        "roaming_dostupny": zone != "Bez roamingu",
        "eu_regulacia": zone in ("Zóna 0", "Zóna 1"),
        "specialne_upozornenia": [],
        "segmenty": {
            "pausal": _segment(prices, ["3 GB do sveta", "50 minút + 50 správ (SMS/MMS)"]),
            "dobijacia_karta": _segment(prices, ["3 GB do sveta"]),
            "bez_zavazkov": _segment(None, ["3 GB do sveta"]),
        },
        "siete": [],
    }


@pytest.fixture
def catalog() -> RoamingCatalog:
    snapshot = {
        "zdroj": "https://www.telekom.sk/volania/roaming",
        "stiahnute": "2026-07-06",
        "krajiny": [
            _country("Turecko", "Zóna 2", aliases=["Turkey", "Türkiye"], iso2="TR", iso3="TUR"),
            _country("Rakúsko", "Zóna 0", aliases=["Austria"], iso2="AT", iso3="AUT"),
            _country("Guinea", "Zóna 3"),
            _country("Guinea Bissau", "Zóna 3"),
            _country("Kuba", "Bez roamingu"),
        ],
    }
    return RoamingCatalog(snapshot)


@pytest.fixture
def tools(catalog: RoamingCatalog) -> dict[str, Any]:
    mcp = _FakeMCP()
    register_roaming_tools(mcp=mcp, catalog=catalog)  # type: ignore[arg-type]
    return mcp.registered


# --- catalog matching --------------------------------------------------------


def test_find_exact_name_without_diacritics(catalog: RoamingCatalog):
    match, alternatives = catalog.find("rakusko")
    assert match is not None
    assert match["nazov"] == "Rakúsko"
    assert alternatives == []


def test_find_by_english_alias_and_iso_codes(catalog: RoamingCatalog):
    for query in ("Turkey", "türkiye", "TR", "tur"):
        match, _ = catalog.find(query)
        assert match is not None, query
        assert match["nazov"] == "Turecko", query


def test_find_exact_name_wins_over_substring(catalog: RoamingCatalog):
    match, alternatives = catalog.find("Guinea")
    assert match is not None
    assert match["nazov"] == "Guinea"
    assert alternatives == []


def test_find_substring_ambiguous(catalog: RoamingCatalog):
    match, alternatives = catalog.find("guine")
    assert match is None
    assert {c["nazov"] for c in alternatives} == {"Guinea", "Guinea Bissau"}


def test_find_fuzzy_suggestion_for_typo(catalog: RoamingCatalog):
    match, alternatives = catalog.find("tureco")
    assert match is None
    assert any(c["nazov"] == "Turecko" for c in alternatives)


def test_find_unknown_and_blank(catalog: RoamingCatalog):
    assert catalog.find("xyzzy12345") == (None, [])
    assert catalog.find("   ") == (None, [])


def test_country_payload_segment_filter(catalog: RoamingCatalog):
    country, _ = catalog.find("Turecko")
    payload = country_payload(country, "pausal")
    assert list(payload["segmenty"]) == ["pausal"]
    assert "slug" not in payload
    full = country_payload(country)
    assert set(full["segmenty"]) == set(SEGMENTS)


# --- roaming_info tool -------------------------------------------------------


async def test_roaming_info_found_all_segments(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="Turecko"))
    assert payload["found"] is True
    assert payload["zona"] == "Zóna 2"
    assert payload["eu_regulacia"] is False
    assert set(payload["segmenty"]) == set(SEGMENTS)
    assert payload["aktualne_k"] == "2026-07-06"


async def test_roaming_info_segment_cut(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="TR", typ_zakaznika="dobijacia_karta"))
    assert payload["found"] is True
    assert list(payload["segmenty"]) == ["dobijacia_karta"]
    names = [b["nazov"] for b in payload["segmenty"]["dobijacia_karta"]["balicky"]]
    assert names == ["3 GB do sveta"]


async def test_roaming_info_eu_country(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="Austria"))
    assert payload["found"] is True
    assert payload["eu_regulacia"] is True


async def test_roaming_info_no_roaming_country(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="Kuba"))
    assert payload["found"] is True
    assert payload["roaming_dostupny"] is False


async def test_roaming_info_ambiguous(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="guine"))
    assert payload["found"] is False
    assert payload["error"] == "ambiguous"
    assert {c["nazov"] for c in payload["kandidati"]} == {"Guinea", "Guinea Bissau"}


async def test_roaming_info_single_fuzzy_suggestion_is_accepted(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="rakuskoo"))
    assert payload["found"] is True
    assert payload["nazov"] == "Rakúsko"


async def test_roaming_info_not_found(tools: dict[str, Any]):
    payload = json.loads(await tools[INFO_TOOL](krajina="xyzzy12345"))
    assert payload["found"] is False
    assert payload["error"] == "not_found"
    assert payload["navrhy"] == []


# --- roaming_zoznam_krajin tool ----------------------------------------------


async def test_zoznam_krajin_unfiltered(tools: dict[str, Any]):
    payload = json.loads(await tools[LIST_TOOL]())
    assert payload["pocet"] == 5
    assert payload["zona"] is None


async def test_zoznam_krajin_zone_filter(tools: dict[str, Any]):
    payload = json.loads(await tools[LIST_TOOL](zona="3"))
    assert payload["zona"] == "Zóna 3"
    assert {c["nazov"] for c in payload["krajiny"]} == {"Guinea", "Guinea Bissau"}
    no_roaming = json.loads(await tools[LIST_TOOL](zona="bez_roamingu"))
    assert {c["nazov"] for c in no_roaming["krajiny"]} == {"Kuba"}


# --- bundled snapshot (structural) -------------------------------------------


@pytest.fixture(scope="module")
def bundled() -> RoamingCatalog:
    return RoamingCatalog.load()


def test_bundled_snapshot_size_and_zones(bundled: RoamingCatalog):
    assert len(bundled.countries) >= 250
    assert {c["zona"] for c in bundled.countries} <= KNOWN_ZONES
    assert bundled.source
    assert bundled.snapshot_date


def test_bundled_snapshot_structure(bundled: RoamingCatalog):
    priced = 0
    for country in bundled.countries:
        assert country["nazov"]
        assert set(country["segmenty"]) == set(SEGMENTS)
        for segment in country["segmenty"].values():
            assert isinstance(segment["balicky"], list)
            for package in segment["balicky"]:
                assert package["nazov"]
        prices = country["segmenty"]["pausal"]["ceny"]
        if prices is not None:
            priced += 1
            assert all(v is None or isinstance(v, float) for v in prices.values())
    assert priced >= 100


def test_bundled_snapshot_well_known_countries(bundled: RoamingCatalog):
    for query, expected_zone in (
        ("Turecko", "Zóna 2"),
        ("USA", "Zóna 2"),
        ("Rakúsko", "Zóna 0"),
        ("Velka Britania", "Zóna 0"),
        ("Svajciarsko", "Zóna 2"),
    ):
        match, _ = bundled.find(query)
        assert match is not None, query
        assert match["zona"] == expected_zone, query
