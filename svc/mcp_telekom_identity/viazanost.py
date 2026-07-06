"""Agreement (viazanosť) parsing and classification from Product Inventory data.

Pure functions — the ``over_viazanost`` tool in :mod:`.tools` fetches the products
and calls these to filter active agreements and classify the commitment window.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

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
