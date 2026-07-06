"""Authentication factor logic: credited factors, next step, and per-factor checks.

The stateful tool itself (``autentifikacia``) lives in :mod:`.tools`; this module
holds the pure decision helpers plus the read-only :func:`_peek_next_auth_factor`
and the state serializer :func:`_persist_auth_state`.
"""

from __future__ import annotations

import re
import unicodedata

from svc.mcp_telekom_identity import widgets
from svc.mcp_telekom_identity._state import _AUTH_STATE, _IDENTITY_STATE
from svc.mcp_telekom_identity.candidates import _RC_LAST4_LEN, _json
from svc.mcp_telekom_identity.classify import _normalize_msisdn
from svc.mcp_telekom_identity.nlp_state import _CHANNEL_CHAT, _nlp_get_named_entities

_MAX_AUTH_ATTEMPTS_PER_FACTOR = 3

# Factor names (use as dict keys + in factors_satisfied set + as "next_factor" value)
_FACTOR_TRUSTED_SOURCE = "trusted_source"
_FACTOR_NAME = "name"
_FACTOR_KOD_ADRESATA = "kod_adresata"
_FACTOR_RC_LAST4 = "rc_last4"
_FACTOR_ORDER = (
    _FACTOR_TRUSTED_SOURCE,
    _FACTOR_NAME,
    _FACTOR_KOD_ADRESATA,
    _FACTOR_RC_LAST4,
)

_AUTH_TYPE_STANDARD = "standard"
_AUTH_TYPE_SENSITIVE = "sensitive"


def _strip_diacritics(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c))


def _normalize_name_for_match(s: str) -> str:
    """Lower-case, strip diacritics, collapse whitespace, sort tokens.

    'Stano Muziková' → 'muzikova stano'  (sorted tokens)
    'MUZIKOVÁ STANO' → 'muzikova stano'  (same)
    """
    if not s:
        return ""
    cleaned = _strip_diacritics(s).lower()
    tokens = [t for t in re.split(r"\s+", cleaned.strip()) if t]
    return " ".join(sorted(tokens))


def _check_name(provided: str, candidate_name: str | None) -> bool:
    if not candidate_name:
        return False
    return _normalize_name_for_match(provided) == _normalize_name_for_match(candidate_name)


def _check_kod_adresata(provided: str, billing_account_ids: list[str]) -> bool:
    val = (provided or "").strip()
    if not val:
        return False
    return val in billing_account_ids


def _check_rc_last4(provided: str, candidate_rc_last4: str | None) -> bool:
    if not candidate_rc_last4:
        return False
    digits = re.sub(r"\D", "", provided or "")
    if len(digits) < _RC_LAST4_LEN:
        return False
    return digits[-_RC_LAST4_LEN:] == candidate_rc_last4


def _normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def _check_trusted_source(input_source: str, contacts: list[dict[str, str]]) -> bool:
    """Compare input_source against candidate contacts (mobile / email)."""
    if not input_source:
        return False
    src = input_source.strip()
    if not src:
        return False

    # Try MSISDN normalization first
    src_msisdn = _normalize_msisdn(src)
    src_email = _normalize_email(src) if "@" in src else None

    for c in contacts:
        ctype = c.get("type")
        value = c.get("value")
        if not value:
            continue
        if ctype == "mobile":
            contact_msisdn = _normalize_msisdn(value)
            if src_msisdn and contact_msisdn and src_msisdn == contact_msisdn:
                return True
        elif ctype == "email" and src_email and _normalize_email(value) == src_email:
            return True
    return False


def _credited_factors_from_identification(
    method: str,
    value: str,
    contacts: list[dict[str, str]],
    input_source: str,
) -> set[str]:
    """Compute which factors are automatically satisfied at the start of auth.

    - factor 1 (trusted_source): always re-evaluated against input_source vs contacts.
    - factor 3 (kod_adresata): satisfied if the identification was kod_zakaznika with
      a billing-account-shaped value (last digit 1-9, i.e. NOT customer id).
    - factor 4 (rc_last4): satisfied if the identification was rodne_cislo (caller proved
      knowledge of the full RČ, last 4 trivially covered).
    """
    credited: set[str] = set()

    if _check_trusted_source(input_source, contacts):
        credited.add(_FACTOR_TRUSTED_SOURCE)

    if method == "kod_zakaznika":
        v = (value or "").strip()
        if v and v[-1] != "0":
            credited.add(_FACTOR_KOD_ADRESATA)
        # ending in 0 → customer id, not kod_adresata; do NOT credit

    if method == "rodne_cislo":
        credited.add(_FACTOR_RC_LAST4)

    return credited


def _next_factor(blocked: set[str]) -> str | None:
    """Return the next factor (in fixed order 1→2→3→4) not in ``blocked``.

    ``blocked`` is the union of factors that are satisfied, failed, or skipped —
    anything we won't ask the customer about again.
    """
    for f in _FACTOR_ORDER:
        if f not in blocked:
            return f
    return None


def _required_factors(auth_type: str) -> int:
    return 3 if auth_type == _AUTH_TYPE_SENSITIVE else 2


def _suggested_response_for_factor(factor: str) -> str:
    return {
        _FACTOR_NAME: "Pre overenie totožnosti mi, prosím, povedzte vaše meno a priezvisko.",
        _FACTOR_KOD_ADRESATA: "Povedzte mi, prosím, kód adresáta — nájdete ho na vašej faktúre.",
        _FACTOR_RC_LAST4: "Povedzte mi, prosím, posledné 4 cifry vášho rodného čísla.",
        _FACTOR_TRUSTED_SOURCE: "Pre overenie totožnosti mi povedzte ďalšiu informáciu.",
    }.get(factor, "Potrebujem ďalší overovací údaj.")


def _instruction_for_factor(factor: str) -> str:
    param = {
        _FACTOR_NAME: "meno_priezvisko",
        _FACTOR_KOD_ADRESATA: "kod_adresata",
        _FACTOR_RC_LAST4: "rc_last4",
    }.get(factor)
    if param:
        return (
            f"Počkaj na odpoveď zákazníka a zavolaj autentifikacia s parametrom {param}=<odpoveď>. "
            f"Ak zákazník daný údaj nemá, zavolaj autentifikacia(skip_current_factor=True)."
        )
    return "Zavolaj autentifikacia bez parametrov pre opätovné vyhodnotenie."


def _need_factor_response(
    next_factor: str,
    *,
    auth_type: str,
    satisfied: set[str],
    required: int,
    channel: str,
) -> str:
    """Return the "need this factor next" evaluation as JSON (never a widget).

    Rendering is the job of ``zobraz_autentifikacny_widget``. In a chat channel the
    ``instruction`` tells the LLM to show that widget; otherwise to collect the value
    via a parameter.
    """
    if channel == _CHANNEL_CHAT and next_factor in widgets.AUTH_FIELD_KEYS:
        instruction = (
            "V chat kanáli zobraz zobraz_autentifikacny_widget a počkaj na odoslanie zákazníkom."
        )
    else:
        instruction = _instruction_for_factor(next_factor)
    return _json(
        {
            "authenticated": False,
            "level_required": auth_type,
            "factors_satisfied": sorted(satisfied),
            "factors_remaining": required - len(satisfied),
            "next_factor": next_factor,
            "suggested_response": _suggested_response_for_factor(next_factor),
            "instruction": instruction,
        }
    )


def _peek_next_auth_factor(conv: str) -> str | None:
    """Read-only: the next customer-facing auth factor, or None if not applicable.

    Mirrors ``autentifikacia``'s credit + blocked computation WITHOUT mutating any
    state. Returns ``name`` / ``kod_adresata`` / ``rc_last4``; returns None when
    there is no single identification, auth is already complete, or only the
    automatic ``trusted_source`` factor would remain.
    """
    identity = _IDENTITY_STATE.get(conv)
    if not identity:
        return None
    candidates = identity.get("candidates") or []
    if len(candidates) != 1:
        return None
    candidate = candidates[0]

    state = _AUTH_STATE.get(conv) or {}
    satisfied = set(state.get("factors_satisfied") or [])
    failed = set(state.get("factors_failed") or [])
    skipped = set(state.get("factors_skipped") or [])

    nlp = _nlp_get_named_entities(conv)
    input_source = nlp.get("input_source", "")
    auth_type = nlp.get("authentication_type") or _AUTH_TYPE_STANDARD
    if auth_type not in (_AUTH_TYPE_STANDARD, _AUTH_TYPE_SENSITIVE):
        auth_type = _AUTH_TYPE_STANDARD

    satisfied |= _credited_factors_from_identification(
        identity.get("identification_method") or "",
        identity.get("identification_value") or "",
        candidate.get("contacts") or [],
        input_source,
    )
    if len(satisfied) >= _required_factors(auth_type):
        return None  # already authenticated

    blocked = satisfied | failed | skipped
    if _FACTOR_TRUSTED_SOURCE not in satisfied and not input_source:
        blocked = blocked | {_FACTOR_TRUSTED_SOURCE}
    nxt = _next_factor(blocked)
    if nxt == _FACTOR_TRUSTED_SOURCE:  # automatic — the customer can't act on it
        nxt = _next_factor(blocked | {_FACTOR_TRUSTED_SOURCE})
    return nxt if nxt in widgets.AUTH_FIELD_KEYS else None


def _persist_auth_state(conv: str, state: dict) -> None:
    """Serialise sets to lists before storing in TTLStore (so retrieval is sane)."""
    serialised = dict(state)
    for key in ("factors_satisfied", "factors_failed", "factors_skipped"):
        if isinstance(serialised.get(key), set):
            serialised[key] = sorted(serialised[key])
    _AUTH_STATE.set(conv, serialised)
