"""Sync layer between our tools and the NLP engine's per-conversation named_entities.

Read path (:func:`_nlp_load`) re-fetches named_entities into the local mirror on
every tool call; write path (:func:`_nlp_flush`) pushes back exactly the entities
*our tools wrote* (never the GET-ed conversation state). The stores themselves
live in :mod:`._state` so they survive across modules and test resets.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import httpx

from svc.mcp_telekom_identity._state import (
    _NLP_CONSUMED_STATE,
    _NLP_MIRROR_STATE,
    _NLP_PENDING_STATE,
)

_log = logging.getLogger(__name__)

# Channel is an NLP-provided named_entity; "chat" turns on widget rendering.
_CHANNEL_KEY = "Channel"
_CHANNEL_CHAT = "chat"

_NLP_BASE_URL = os.environ.get("APP_GOODBOT_URL") or os.environ.get(
    "GOODBOT_URL", "http://goodbot.internal-test.svc.cluster.local:8121"
)
_NLP_TIMEOUT_SECONDS = 1.0

# Widget-submitted raw inputs land in the mirror (the host writes them into
# named_entities on submit). They are never pushed back to the NLP engine — they
# may contain sensitive identifiers (rodné číslo, auth secrets). We read them,
# use them, and let them expire with the mirror; the LLM never sees them.
_DO_NOT_FLUSH_KEYS = frozenset(
    {
        "identifikacia_vstup",
        "identifikacia_typ",
        "autentifikacia_meno_priezvisko",
        "autentifikacia_kod_adresata",
        "autentifikacia_rc_last4",
    }
)


def _nlp_set_state(conversation_id: str, named_entities: dict[str, Any]) -> None:
    """Record named_entities written *by our tools*.

    Two stores are updated:

    - :data:`_NLP_MIRROR_STATE` — the read mirror, so later reads in the same turn
      see the tool's write (alongside whatever was GET-ed from the NLP engine).
    - :data:`_NLP_PENDING_STATE` — the *only* thing :func:`_nlp_flush` pushes back.
      We push exactly the entities our tools created/changed, never the large set
      of state GET-ed from the NLP engine (gpt_history, channel, …).
    """
    if not conversation_id:
        return
    # Coerce to str; named_entities only carries scalars.
    coerced = {k: str(v) for k, v in named_entities.items()}

    current = _NLP_MIRROR_STATE.get(conversation_id) or {}
    current.update(coerced)
    _NLP_MIRROR_STATE.set(conversation_id, current)

    pending = _NLP_PENDING_STATE.get(conversation_id) or {}
    pending.update(coerced)
    _NLP_PENDING_STATE.set(conversation_id, pending)


def _nlp_get_named_entities(conversation_id: str) -> dict[str, str]:
    """Read mirrored NLP named_entities for the conversation."""
    if not conversation_id:
        return {}
    return _NLP_MIRROR_STATE.get(conversation_id) or {}


def _consume_named_entity(conversation_id: str, key: str) -> str | None:
    """Return a widget-submitted named_entity once per distinct value.

    Widget values are consumed once so a later call doesn't re-apply a stale value
    (e.g. re-verifying an already-handled auth factor). Because :func:`_nlp_load`
    re-fetches named_entities from the NLP engine on every tool call (the customer
    may submit a widget *between* calls), popping from the mirror is not enough —
    the next GET would simply re-supply the same value. Instead we remember the
    (key, value) already consumed and refuse to hand the *same* value out again; a
    genuinely new value (re-submission/correction) is consumed afresh.
    """
    if not conversation_id:
        return None
    current = _NLP_MIRROR_STATE.get(conversation_id) or {}
    value = current.get(key)
    if value is None:
        return None
    consumed = _NLP_CONSUMED_STATE.get(conversation_id) or {}
    if consumed.get(key) == value:
        return None  # already consumed this exact value
    consumed[key] = value
    _NLP_CONSUMED_STATE.set(conversation_id, consumed)
    return value


async def _nlp_load(conversation_id: str) -> None:
    """Refresh the local read-mirror from the NLP engine on every tool call.

    The customer may submit a widget *between* tool calls — the host then writes
    the field values (e.g. ``identifikacia_vstup``) into the conversation's
    named_entities on the NLP engine. So we must re-fetch every time rather than
    trusting a warm cache (a stale warm mirror was why a freshly-submitted widget
    value was invisible to the tool). Freshly fetched server entities are merged
    *over* the mirror; locally-held keys the server does not return — test context
    (:func:`nastav_test_kontext`) or tool writes not yet flushed — are preserved.

    Falls back gracefully — 400/404 (no session) and network errors are logged and
    leave the existing mirror untouched.
    """
    if not conversation_id:
        return

    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/named_entities"
    try:
        async with httpx.AsyncClient(timeout=_NLP_TIMEOUT_SECONDS) as http:
            resp = await http.get(url)
        if resp.status_code == 200:
            entities = {
                k: str(v)
                for k, v in (resp.json().get("named_entities") or {}).items()
            }
            if entities:
                current = dict(_NLP_MIRROR_STATE.get(conversation_id) or {})
                current.update(entities)
                _NLP_MIRROR_STATE.set(conversation_id, current)
                _log.info("NLP GET named_entities %s -> merged %d entities", url, len(entities))
        elif resp.status_code in (400, 404):
            _log.debug("NLP GET named_entities %s -> %s (no session)", url, resp.status_code)
        else:
            _log.warning("NLP GET named_entities %s -> %s", url, resp.status_code)
    except Exception as exc:
        _log.warning("NLP GET named_entities %s -> error: %s", url, exc)


def _nlp_flush(conversation_id: str) -> None:
    """Fire-and-forget PUT of the entities *our tools wrote* to the NLP engine.

    We push exactly :data:`_NLP_PENDING_STATE` — the named_entities accumulated by
    :func:`_nlp_set_state` — and nothing else. No delta against the GET-ed state is
    computed, so the large set of conversation state mirrored from the NLP engine
    (gpt_history, channel, …) is never echoed back.

    Sensitive widget inputs (:data:`_DO_NOT_FLUSH_KEYS`) are filtered out as a
    safety net. On a successful PUT the pushed keys are removed from the pending
    buffer; on failure they stay and are retried on the next flush. Retries up to
    3 times on 429 honouring Retry-After. Errors are logged and never raised.
    """
    if not conversation_id:
        return
    pending = dict(_NLP_PENDING_STATE.get(conversation_id) or {})
    to_push = {k: v for k, v in pending.items() if k not in _DO_NOT_FLUSH_KEYS}
    if not to_push:
        return

    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/states"
    payload = {"named_entities": to_push}
    body = json.dumps(payload, ensure_ascii=False).encode()

    def _do() -> None:
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            req = urllib.request.Request(
                url, data=body, method="PUT",
                headers={"Content-Type": "application/json"},
            )
            _log.info("NLP state PUT %s named_entities=%s", url, json.dumps(payload, ensure_ascii=False))
            try:
                with urllib.request.urlopen(req, timeout=_NLP_TIMEOUT_SECONDS) as resp:
                    _log.info("NLP state PUT %s -> HTTP %s", url, resp.status)
                    # Drop the pushed keys from the pending buffer (keep any added since).
                    remaining = {
                        k: v
                        for k, v in (_NLP_PENDING_STATE.get(conversation_id) or {}).items()
                        if k not in to_push
                    }
                    _NLP_PENDING_STATE.set(conversation_id, remaining)
                    return
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < max_retries:
                    retry_after = float(
                        exc.headers.get("Retry-After") or exc.headers.get("retry-after") or "1"
                    )
                    _log.warning(
                        "NLP state PUT %s -> 429, retry in %.1fs (%d/%d)",
                        url, retry_after, attempt, max_retries,
                    )
                    time.sleep(retry_after)
                else:
                    _log.warning("NLP state PUT %s -> HTTP %s", url, exc.code)
                    return
            except Exception as exc:
                _log.warning("NLP state PUT %s -> error: %s", url, exc)
                return

    threading.Thread(target=_do, daemon=True, name="nlp-state-put-identity").start()
