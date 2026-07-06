"""Refresh the bundled roaming snapshot from www.telekom.sk.

The public page https://www.telekom.sk/volania/roaming is a Next.js app whose
server-rendered "flight" payload embeds the full Storyblok CMS roaming dataset:
279 ``roaming_country`` entries, each pointing to a shared ``roaming_zone``
(Zóna 0-4 / "Bez roamingu") that carries per-segment prices (paušál = postpaid,
dobíjacia karta = prepaid, Bez záväzkov = contract-free) and roaming packages.
Network coverage (operators + 2G-5G/VoLTE) comes from a public CloudFront JSON.

This script fetches both sources, merges them into the compact snapshot used by
:mod:`.roaming` at runtime and writes it to ``data/roaming_sk.json``. Run it
whenever Telekom changes roaming prices or packages:

    python -m svc.mcp_telekom_identity.roaming_refresh

The parsing of the flight payload is intentionally confined to this script so
the runtime tool never depends on the (fragile) page structure. Validation is
strict: when the page layout changes, the script fails loudly instead of
writing a truncated snapshot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from svc.mcp_telekom_identity.roaming import DATA_PATH, SEGMENTS, normalize_name

_log = logging.getLogger(__name__)

ROAMING_PAGE_URL = "https://www.telekom.sk/volania/roaming"
COVERAGE_URL = "https://d25083sye52snf.cloudfront.net/roaming/data.json"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_FLIGHT_CHUNK_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_COUNTRIES_ARRAY_RE = re.compile(r'"countries":\[')

_EU_ZONES = frozenset({"Zóna 0", "Zóna 1"})
_KNOWN_ZONES = frozenset({"Zóna 0", "Zóna 1", "Zóna 2", "Zóna 3", "Zóna 4", "Bez roamingu"})
_NO_ROAMING_ZONE = "Bez roamingu"

# Segment key -> (CMS prefix for prices/text, CMS prefix for packages/notices).
# The prefixes differ for the contract-free segment because of a typo baked
# into the CMS schema: text is "contract_free_*", packages are "contact_free_*".
_SEGMENT_CMS_PREFIXES: dict[str, tuple[str, str]] = {
    "pausal": ("postpaid", "postpaid"),
    "dobijacia_karta": ("prepaid", "prepaid"),
    "bez_zavazkov": ("contract_free", "contact_free"),
}

_MIN_COUNTRIES = 250
_MIN_PRICED_COUNTRIES = 100


def fetch_text(url: str) -> str:
    """Download ``url`` with a browser User-Agent (telekom.sk blocks bare clients)."""
    response = httpx.get(
        url, headers={"User-Agent": _USER_AGENT}, timeout=30.0, follow_redirects=True
    )
    response.raise_for_status()
    return response.text


def reassemble_flight_payload(html: str) -> str:
    """Join the Next.js ``self.__next_f.push([1,"..."])`` chunks into one string.

    Country objects can straddle chunk boundaries, so the chunks must be
    JSON-string-decoded and concatenated before any extraction.
    """
    chunks = _FLIGHT_CHUNK_RE.findall(html)
    if not chunks:
        msg = "no Next.js flight chunks found - has the page structure changed?"
        raise ValueError(msg)
    return "".join(json.loads(f'"{chunk}"') for chunk in chunks)


def extract_countries(payload: str) -> list[dict[str, Any]]:
    """Decode the largest ``"countries":[...]`` array (the roaming picker dataset).

    The picker is rendered more than once (desktop/mobile); all occurrences are
    decoded and the largest wins.
    """
    decoder = json.JSONDecoder()
    best: list[dict[str, Any]] = []
    for match in _COUNTRIES_ARRAY_RE.finditer(payload):
        try:
            array, _ = decoder.raw_decode(payload, match.end() - 1)
        except ValueError:
            continue
        if isinstance(array, list) and len(array) > len(best):
            best = [entry for entry in array if isinstance(entry, dict)]
    if not best:
        msg = "no roaming countries array found in the page payload"
        raise ValueError(msg)
    return best


def _plain_text(node: Any) -> str:
    """Render a Storyblok rich-text ``doc`` node to plain text."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return str(node.get("text") or "")
    children = node.get("content") or []
    if node.get("type") == "paragraph":
        return "".join(_plain_text(child) for child in children)
    rendered = (_plain_text(child) for child in children)
    return "\n".join(text for text in rendered if text)


def _notice_texts(*notice_lists: Any) -> list[str]:
    """Flatten CMS notification blocks into deduplicated "Title: description" strings."""
    texts: list[str] = []
    for notices in notice_lists:
        if not isinstance(notices, list):
            continue
        for notice in notices:
            if not isinstance(notice, dict):
                continue
            title = str(notice.get("title") or "").strip()
            description = str(notice.get("description") or "").strip()
            text = f"{title}: {description}" if title and description else title or description
            if text and text not in texts:
                texts.append(text)
    return texts


def _price_eur(value: Any) -> float | None:
    """Parse a CMS price string ('1.9988', '') into EUR or None when absent."""
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return None


def _package(package: dict[str, Any]) -> dict[str, Any]:
    """Shape one CMS ``roaming_package`` into the snapshot package dict."""
    content = package.get("content") or {}
    return {
        "nazov": str(package.get("name") or content.get("title") or ""),
        "cena_eur": _price_eur(content.get("price")),
        "poplatok": str(content.get("price_label") or ""),
        "popis": str(content.get("subtitle") or ""),
    }


def _collect_package_uuids(raw_countries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map package uuid -> package object, used to resolve unresolved CMS references."""
    by_uuid: dict[str, dict[str, Any]] = {}
    for raw in raw_countries:
        content = raw.get("content") or {}
        zone_content = (content.get("zone") or {}).get("content") or {}
        for holder in (content, zone_content):
            for field in (
                "packages",
                "prepaid_packages",
                "postpaid_packages",
                "contact_free_packages",
            ):
                for entry in holder.get(field) or []:
                    if isinstance(entry, dict) and entry.get("uuid"):
                        by_uuid[str(entry["uuid"])] = entry
    return by_uuid


def _resolve_packages(
    entries: Any, package_by_uuid: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return package dicts, resolving bare-uuid string entries via ``package_by_uuid``."""
    resolved: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            resolved.append(entry)
        elif isinstance(entry, str) and entry in package_by_uuid:
            resolved.append(package_by_uuid[entry])
        else:
            _log.warning("skipping unresolved package reference %r", entry)
    return resolved


def _segment_prices(
    zone_content: dict[str, Any], cms_prefix: str
) -> dict[str, float | None] | None:
    """Per-unit prices for a segment; None for segments/zones without a price list."""
    if cms_prefix == "contract_free":
        return None  # the CMS carries no per-unit prices for "Bez záväzkov"
    prices = {
        "odchadzajuci_hovor_eur_min": _price_eur(zone_content.get(f"{cms_prefix}_outgoing")),
        "prichadzajuci_hovor_eur_min": _price_eur(zone_content.get(f"{cms_prefix}_incoming")),
        "sms_eur": _price_eur(zone_content.get(f"{cms_prefix}_sms")),
        "mms_eur": _price_eur(zone_content.get(f"{cms_prefix}_mms")),
        "data_eur_mb": _price_eur(zone_content.get(f"{cms_prefix}_data")),
    }
    if all(value is None for value in prices.values()):
        return None
    return prices


def _segment_packages(
    country_content: dict[str, Any],
    zone_content: dict[str, Any],
    packages_prefix: str,
    package_by_uuid: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge zone-level and country-level packages the same way the web page does.

    ``hide_all_other_packages`` on a country suppresses the zone-level offer
    (used for countries where the "do sveta" data packages don't apply).
    """
    field = f"{packages_prefix}_packages"
    zone_packages = _resolve_packages(zone_content.get("packages"), package_by_uuid)
    zone_packages += _resolve_packages(zone_content.get(field), package_by_uuid)
    country_packages = _resolve_packages(country_content.get("packages"), package_by_uuid)
    country_packages += _resolve_packages(country_content.get(field), package_by_uuid)
    merged = (
        country_packages
        if country_content.get("hide_all_other_packages")
        else zone_packages + country_packages
    )
    packages: list[dict[str, Any]] = []
    for entry in merged:
        shaped = _package(entry)
        if shaped["nazov"] and shaped not in packages:
            packages.append(shaped)
    return packages


def _segment_notes(
    country_content: dict[str, Any], zone_content: dict[str, Any], packages_prefix: str
) -> list[str]:
    """Merge zone-level and country-level notices for one segment."""
    fields = (
        "packages_notice",
        "services_notice",
        f"{packages_prefix}_packages_notice",
        f"{packages_prefix}_services_notice",
    )
    zone_notes = _notice_texts(*(zone_content.get(field) for field in fields))
    country_notes = _notice_texts(*(country_content.get(field) for field in fields))
    if country_content.get("hide_all_other_notices"):
        return country_notes
    return zone_notes + [note for note in country_notes if note not in zone_notes]


def _build_segment(
    segment: str,
    country_content: dict[str, Any],
    zone_content: dict[str, Any],
    package_by_uuid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the snapshot view of one customer segment for one country."""
    text_prefix, packages_prefix = _SEGMENT_CMS_PREFIXES[segment]
    return {
        "ceny": _segment_prices(zone_content, text_prefix),
        "info": _plain_text(zone_content.get(f"{text_prefix}_text")),
        "balicky": _segment_packages(
            country_content, zone_content, packages_prefix, package_by_uuid
        ),
        "poznamky": _segment_notes(country_content, zone_content, packages_prefix),
    }


def _network_technologies(network: dict[str, Any]) -> list[str]:
    """Human-readable technology list ('2G'..'5G', 'VoLTE', 'satelit') for one network."""
    technologies = [
        generation.upper()
        for generation in ("2g", "3g", "4g", "5g")
        if network.get(f"data_{generation}") or network.get(f"voice_{generation}")
    ]
    if network.get("volte_4g"):
        technologies.append("VoLTE")
    if network.get("satellite"):
        technologies.append("satelit")
    return technologies


def build_coverage_index(coverage: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Index the CloudFront coverage JSON by normalized Slovak country name."""
    index: dict[str, list[dict[str, Any]]] = {}
    for entry in coverage.get("roamingData") or []:
        if not isinstance(entry, dict):
            continue
        name = normalize_name(str(entry.get("countryNameSk") or entry.get("country") or ""))
        if not name:
            continue
        index[name] = [
            {
                "operator": str(network.get("operator") or ""),
                "technologie": _network_technologies(network),
            }
            for network in entry.get("networks") or []
            if isinstance(network, dict)
        ]
    return index


def _build_country(
    raw: dict[str, Any],
    coverage_index: dict[str, list[dict[str, Any]]],
    package_by_uuid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Shape one CMS ``roaming_country`` entry into the snapshot country dict."""
    content = raw.get("content") or {}
    zone = content.get("zone") or {}
    zone_content = zone.get("content") or {}
    zone_name = str(zone.get("name") or "")
    name = str(raw.get("name") or content.get("name") or "")
    aliases = [alias.strip() for alias in str(content.get("aliases") or "").split(",")]
    return {
        "nazov": name,
        "slug": str(raw.get("slug") or ""),
        "aliasy": [alias for alias in aliases if alias],
        "iso2": content.get("code_2") or None,
        "iso3": content.get("iso_code") or None,
        "zona": zone_name,
        "roaming_dostupny": zone_name != _NO_ROAMING_ZONE,
        "eu_regulacia": zone_name in _EU_ZONES,
        "specialne_upozornenia": _notice_texts(content.get("special_notice")),
        "segmenty": {
            segment: _build_segment(segment, content, zone_content, package_by_uuid)
            for segment in SEGMENTS
        },
        "siete": coverage_index.get(normalize_name(name), []),
    }


def build_snapshot(raw_countries: list[dict[str, Any]], coverage: dict[str, Any]) -> dict[str, Any]:
    """Merge the CMS countries and the coverage JSON into the runtime snapshot."""
    coverage_index = build_coverage_index(coverage)
    package_by_uuid = _collect_package_uuids(raw_countries)
    countries = [_build_country(raw, coverage_index, package_by_uuid) for raw in raw_countries]
    countries.sort(key=lambda country: normalize_name(country["nazov"]))
    return {
        "zdroj": ROAMING_PAGE_URL,
        "zdroj_pokrytia": COVERAGE_URL,
        "stiahnute": dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%d"),
        "krajiny": countries,
    }


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    """Fail loudly when the extracted dataset looks truncated or structurally off."""
    countries = snapshot["krajiny"]
    if len(countries) < _MIN_COUNTRIES:
        msg = f"only {len(countries)} countries extracted (expected >= {_MIN_COUNTRIES})"
        raise ValueError(msg)
    unknown_zones = {country["zona"] for country in countries} - _KNOWN_ZONES
    if unknown_zones:
        msg = f"unknown roaming zones extracted: {sorted(unknown_zones)}"
        raise ValueError(msg)
    priced = sum(1 for country in countries if country["segmenty"]["pausal"]["ceny"] is not None)
    if priced < _MIN_PRICED_COUNTRIES:
        msg = f"only {priced} countries carry postpaid prices (expected >= {_MIN_PRICED_COUNTRIES})"
        raise ValueError(msg)


def main(argv: list[str] | None = None) -> None:
    """Fetch, parse, validate and write the roaming snapshot."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA_PATH, help="Target snapshot path.")
    parser.add_argument(
        "--page-file",
        type=Path,
        default=None,
        help="Parse a local copy of the roaming page instead of fetching it.",
    )
    parser.add_argument(
        "--coverage-file",
        type=Path,
        default=None,
        help="Parse a local copy of the coverage JSON instead of fetching it.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    html = (
        args.page_file.read_text(encoding="utf-8")
        if args.page_file
        else fetch_text(ROAMING_PAGE_URL)
    )
    coverage_text = (
        args.coverage_file.read_text(encoding="utf-8")
        if args.coverage_file
        else fetch_text(COVERAGE_URL)
    )
    raw_countries = extract_countries(reassemble_flight_payload(html))
    snapshot = build_snapshot(raw_countries, json.loads(coverage_text))
    validate_snapshot(snapshot)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    _log.info(
        "wrote %s countries to %s (%.0f kB)",
        len(snapshot["krajiny"]),
        args.output,
        args.output.stat().st_size / 1024,
    )


if __name__ == "__main__":
    main()
