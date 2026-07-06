"""Process-local conversation state for the identity service.

All transient per-conversation stores live here in ONE place so the rest of the
package (tools / auth / nlp_state) can share them without circular imports, and
so tests can wipe them between cases via :func:`reset_all`.

Important: these store objects are created once at import and **never rebound**.
Other modules ``from ._state import _IDENTITY_STATE`` and keep the reference;
:func:`reset_all` clears entries in place (``TTLStore.clear``) rather than
re-instantiating, so every holder of the reference keeps seeing the same object.
Rebinding any of these names would silently desynchronise the modules.
"""

from __future__ import annotations

from typing import Any

from lib.mcp_service.state import TTLStore

# --- TTLs -------------------------------------------------------------------
_IDENTITY_TTL_SECONDS = 30 * 60
_AUTH_TTL_SECONDS = 30 * 60
_NLP_MIRROR_TTL_SECONDS = 30 * 60

# --- Identification cache ---------------------------------------------------
# Full candidate set + identification method/value for downstream tools.
_IDENTITY_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=_IDENTITY_TTL_SECONDS)

# --- Authentication progress ------------------------------------------------
_AUTH_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=_AUTH_TTL_SECONDS)

# --- NLP named_entities sync ------------------------------------------------
# Read mirror of named_entities (GET-ed from the NLP engine + tool writes + test
# context). In production the read path is a GET /named_entities against the NLP
# engine; this mirror lets tests and `nastav_test_kontext` simulate that state.
_NLP_MIRROR_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)

# named_entities written by our tools that still need to be PUT to the NLP
# engine. _nlp_flush pushes exactly this (never the GET-ed conversation state)
# and clears the pushed keys on success.
_NLP_PENDING_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)

# (key -> value) widget submissions already consumed by _consume_named_entity.
# Because _nlp_load re-fetches named_entities on every call, the same value would
# otherwise be re-applied each time; this guard enforces consume-once-per-value
# while still letting a corrected re-submission (new value) through.
_NLP_CONSUMED_STATE: TTLStore[dict[str, str]] = TTLStore(ttl_seconds=_NLP_MIRROR_TTL_SECONDS)


def reset_all() -> None:
    """Clear every conversation store in place (for test isolation)."""
    for store in (
        _IDENTITY_STATE,
        _AUTH_STATE,
        _NLP_MIRROR_STATE,
        _NLP_PENDING_STATE,
        _NLP_CONSUMED_STATE,
    ):
        store.clear()
