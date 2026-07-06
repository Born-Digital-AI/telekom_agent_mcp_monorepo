"""Identifier normalization, structural validation and type classification.

Pure functions — no I/O, no shared state. Given a free-text identifier the
classifier picks one of the route keys (``_TYPE_*``); the per-type normalizers
canonicalise a value once the type is known.
"""

from __future__ import annotations

import re

# Identifier route keys (also the dropdown values in the widget).
_TYPE_TELEFON = "telefon"
_TYPE_ICO = "ico"
_TYPE_RODNE_CISLO = "rodne_cislo"
_TYPE_KOD_ZAKAZNIKA = "kod_zakaznika"
_TYPE_SERIOVE_CISLO = "seriove_cislo"
_TYPE_AUTO = "auto"

# Rodné číslo: 9 or 10 digits, no slash.
_RC_PATTERN = re.compile(r"^\d{9,10}$")

# Slovak IČO: exactly 8 digits.
_ICO_PATTERN = re.compile(r"^\d{8}$")

# "Kód zákazníka" or "Kód účtu" — numeric code 8-12 digits, no separators.
# Ends in 0  → Customer ID
# Ends in 1-9 → Billing Account ID (resolves to Customer via billingAccount.customer.id)
_KOD_PATTERN = re.compile(r"^\d{8,12}$")

# MSISDN normalization:
# - strip whitespace, dashes, parentheses, dots, leading "+"
# - "0904..." (SK local, 10 digits)  → "421904..."   (replace leading 0 with 421)
# - "00421904..."                    → "421904..."   (strip 00)
# - "+421904..." or "421904..."      → "421904..."   (already intl or with +)
_MSISDN_STRIP_RE = re.compile(r"[\s\-().+]")

# Serial number normalization: strip whitespace, dashes, slashes, dots; uppercase.
# Then validate as alphanumeric 8-30 chars. Real test data is 12 chars but production
# routers/STBs use a wide variety of lengths and formats — keep the pattern permissive.
_SERIAL_STRIP_RE = re.compile(r"[\s\-/.]")
_SERIAL_PATTERN = re.compile(r"^[A-Z0-9]{8,30}$")


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


def _is_valid_ico(digits: str) -> bool:
    """Slovak/Czech IČO checksum: 8 digits, weighted mod-11 over the first 7."""
    if len(digits) != 8 or not digits.isdigit():
        return False
    weights = (8, 7, 6, 5, 4, 3, 2)
    total = sum(int(digits[i]) * weights[i] for i in range(7))
    check = 11 - (total % 11)
    if check == 10:
        check = 0
    elif check == 11:
        check = 1
    return check == int(digits[7])


def _is_valid_rodne_cislo(digits: str) -> bool:
    """Structural RČ validation: 9 or 10 digits, plausible YYMMDD, and (10-digit) mod 11.

    Month accepts the standard 1-12 and +50 (women). The rare post-2003 +20/+70
    extensions are intentionally NOT accepted — they widen the false-positive
    surface (customer/billing codes that happen to parse as a date) more than
    they help. The mod-11 divisibility check (10-digit RČ only) is the main guard
    that keeps a random customer code from being mistaken for a rodné číslo.
    """
    if not digits.isdigit() or len(digits) not in (9, 10):
        return False
    month = int(digits[2:4])
    if month > 50:
        month -= 50
    if not 1 <= month <= 12:
        return False
    day = int(digits[4:6])
    if not 1 <= day <= 31:
        return False
    if len(digits) == 10 and int(digits) % 11 != 0:
        return False
    return True


def _classify_identifier(raw: str) -> tuple[str | None, list[str]]:
    """Classify a free-text identifier into a route key.

    Returns ``(type, [])`` for a confident single match, ``(None, candidates)``
    when two *structured* signals genuinely collide (caller should disambiguate),
    or ``(None, [])`` when nothing plausible matches.

    Priority: a structurally valid serial / phone / IČO / rodné číslo wins;
    ``kod_zakaznika`` (8-12 digits, no checksum) is only the fallback when no
    structured signal fires.
    """
    value = (raw or "").strip()
    if not value:
        return None, []

    # 1. Contains a letter → only the serial number is alphanumeric.
    serial_norm = _SERIAL_STRIP_RE.sub("", value).upper()
    if any(c.isalpha() for c in serial_norm):
        if _SERIAL_PATTERN.fullmatch(serial_norm):
            return _TYPE_SERIOVE_CISLO, []
        return None, []

    # 2. Digits only from here.
    stripped = _MSISDN_STRIP_RE.sub("", value)
    if not stripped or not stripped.isdigit():
        return None, []

    msisdn = _normalize_msisdn(value)
    explicit_phone = msisdn is not None and (
        value.strip().startswith("+")
        or stripped.startswith("00421")
        or stripped.startswith("421")
    )
    if explicit_phone:
        return _TYPE_TELEFON, []

    digits = stripped
    n = len(digits)

    strong: list[str] = []
    if _is_valid_rodne_cislo(digits):
        strong.append(_TYPE_RODNE_CISLO)
    if n == 8 and _is_valid_ico(digits):
        strong.append(_TYPE_ICO)
    if msisdn is not None:  # bare local 0… that normalises to a SK MSISDN
        strong.append(_TYPE_TELEFON)

    if len(strong) == 1:
        return strong[0], []
    if len(strong) >= 2:
        return None, strong  # genuine collision (e.g. 09… valid as phone and RČ)

    # No structured signal → numeric customer/billing code is the fallback.
    if _KOD_PATTERN.fullmatch(digits):
        return _TYPE_KOD_ZAKAZNIKA, []
    return None, []
