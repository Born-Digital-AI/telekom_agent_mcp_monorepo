"""Shaping of DPS Party/Customer records into the tool-facing ``candidate`` dict.

Also holds the shared JSON serializer (:func:`_json`) and the DPS-error →
response-payload mapping (:func:`_dps_error_payload`).
"""

from __future__ import annotations

import json
from typing import Any

from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)

_UPSTREAM_ERROR_MESSAGE = "Vyskytol sa technický problém. Prepojím vás na operátora."

# Auth factor "rc_last4" compares the last 4 digits of the rodné číslo.
_RC_LAST4_LEN = 4


def _json(obj: Any) -> str:  # noqa: ANN401 — generic JSON serialiser
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
            if isinstance(value, str) and len(value) >= _RC_LAST4_LEN:
                return value[-_RC_LAST4_LEN:]
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
