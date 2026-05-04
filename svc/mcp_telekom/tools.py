"""Telekom intent recognition MCP tools.

get_routes()
— Lightweight overview of the 4 routing destinations + key signals.
Use as first call to determine broad category.

get_intents_for_route(route)
— Full intent list for one specific route only (~25% of full catalog).
Call after get_routes() once you know the broad category.

get_clarification_questions(area)
— Disambiguation Q&A script for a specific tricky area.

resolve_intent(utterance, intent_id, summary, confidence, follow_up_question)
— Validates and formats the final classification. Always call last.

get_topic_tree()  [DEBUG ONLY]
— Full catalog dump. Do not use in production voice flows.
"""

from __future__ import annotations

import json
import logging
import os
import unicodedata
import urllib.error
import urllib.request
from typing import Any

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

from .clarification_scripts import CLARIFICATION_SCRIPTS
from .intent_catalog import (
    DISAMBIGUATION_AREAS,
    INTENT_CATALOG,
    INTENT_ROUTE,
    INTENTS_BY_ROUTE,
    ROUTE_DESCRIPTIONS,
    SPECIAL_PRIORITY_RULES,
)

_NLP_BASE_URL = os.environ.get("GOODBOT_URL", "http://goodbot.internal-test.svc.cluster.local:8121")
_log = logging.getLogger(__name__)


def _nlp_set_state(conversation_id: str, named_entities: dict[str, Any]) -> None:
    """Fire-and-forget PUT to NLP engine state endpoint."""
    url = f"{_NLP_BASE_URL}/conversations/{conversation_id}/states"
    payload = {"named_entities": named_entities}
    body = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(
        url, data=body, method="PUT", headers={"Content-Type": "application/json"}
    )
    _log.info("NLP state PUT %s body=%s", url, json.dumps(payload, ensure_ascii=False))
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            _log.info("NLP state PUT %s → HTTP %s body=%s", url, resp.status, resp_body)
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        _log.warning("NLP state PUT %s → HTTP %s body=%s", url, exc.code, resp_body)
    except Exception as exc:
        _log.warning("NLP state PUT %s → error: %s", url, exc)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _check_special_priority(utterance: str) -> dict[str, Any] | None:
    """Return override dict if utterance matches a special priority rule, else None."""
    normalized = utterance.lower()
    ascii_norm = unicodedata.normalize("NFD", normalized).encode("ascii", "ignore").decode("ascii")
    for rule in SPECIAL_PRIORITY_RULES:
        for pattern in rule["trigger_patterns"]:
            pat_ascii = (
                unicodedata.normalize("NFD", pattern.lower())
                .encode("ascii", "ignore")
                .decode("ascii")
            )
            if pat_ascii in ascii_norm:
                return rule["override"]
    return None


# Key signals per route — concise, for fast initial routing decision
_ROUTE_SIGNALS: dict[str, list] = {
    "selfcare": [
        "faktúra, platba, dlh, preplatok, inkaso, upomienka",
        "spotreba dát/minút, roaming, pin/puk, pid kód",
        "dobitie kreditu, výpis hovorov, rodičovský zámok",
        "voyo, smart karta, telekom centrum, swipe",
    ],
    "standard_contact_center": [
        "aktivácia, zmena paušálu/programu, zrušenie zmluvy, viazanosť",
        "portácia, esim, sim karta, tv balíčky, netflix",
        "reklamácia, poistenie, nový telefón, objednávka",
        "magio go heslo, zmena fakturačných údajov, kuriér",
        "zákazník bol kontaktovaný telekomom, operátor, výmena routera (SMS notifikácia)",
    ],
    "tech_selfcare": [
        "nefunguje / nejde: internet, tv, pevná linka, mobilná linka, mobilné dáta",
        "pomalý internet, výpadky internetu",
        "router nefunguje / pokazený (nie výmena na základe SMS)",
        "wifi signál, nastavenie wifi",
        "porucha, výpadok, technická podpora — tieto výrazy VŽDY sem",
    ],
    "tech_contact_center": [
        "padnutý stĺp, poškodený kábel, infraštruktúra",
        "fyzická porucha telefónneho aparátu",
        "komplikovaný technický problém mimo bežného katalógu",
    ],
}


def _maybe_update_nlp_state(
    utterance: str,
    result: dict[str, Any],
    meta: dict[str, Any] | None,
) -> None:
    """Update NLP engine conversation state with resolved intent (fire-and-forget)."""
    if not meta:
        return
    conversation_id = meta.get("conversation_id")
    if not conversation_id:
        _log.warning("NLP state update skipped: no conversation_id in _meta %s", meta)
        return
    _nlp_set_state(
        conversation_id,
        {
            "utterance": utterance,
            "intent_id": result["intent_id"],
            "summary": result["summary"],
            "confidence": str(result["confidence"]),
            "follow_up_question": result["follow_up_question"],
        },
    )


def register(registry: ToolRegistry) -> None:

    @mcp_tool(
        name="get_routes",
        description=(
            "Returns routing reference data (4 destinations, key signals, special rules). "
            "Call this ONLY when you genuinely cannot determine the route from the utterance alone. "
            "For most utterances you should call resolve_intent() directly. "
            "Routes: selfcare | standard_contact_center | tech_selfcare | tech_contact_center."
        ),
        registry=registry,
    )
    def get_routes() -> str:
        routes_payload = {}
        for route, description in ROUTE_DESCRIPTIONS.items():
            routes_payload[route] = {
                "description": description,
                "key_signals": _ROUTE_SIGNALS.get(route, []),
                "intent_count": len(INTENTS_BY_ROUTE.get(route, [])),
            }
        return _json(
            {
                "routes": routes_payload,
                "special_priority_rules": SPECIAL_PRIORITY_RULES,
                "disambiguation_areas": DISAMBIGUATION_AREAS,
                "next_step": "Now call resolve_intent() if you know the intent, or get_clarification_questions() if ambiguous.",
            }
        )

    @mcp_tool(
        name="get_intents_for_route",
        description=(
            "Returns the full intent list for ONE specific route. "
            "Call after get_routes() once you have determined the broad routing category. "
            "Valid route values: selfcare | standard_contact_center | tech_selfcare | tech_contact_center. "
            "Returns: intent IDs, labels, descriptions, keywords, excludes, and disambiguation hints."
        ),
        registry=registry,
    )
    def get_intents_for_route(route: str) -> str:
        route = route.strip()
        if route not in ROUTE_DESCRIPTIONS:
            return _json(
                {
                    "error": f"Unknown route: '{route}'",
                    "valid_routes": list(ROUTE_DESCRIPTIONS.keys()),
                }
            )
        intents = []
        for intent_id in INTENTS_BY_ROUTE.get(route, []):
            data = INTENT_CATALOG[intent_id]
            intents.append(
                {
                    "id": intent_id,
                    "label": data["label"],
                    "description": data["description"],
                    "keywords": data["keywords"],
                    "excludes": data["excludes"],
                    "disambiguate": data["disambiguate"],
                }
            )
        return _json(
            {
                "route": route,
                "description": ROUTE_DESCRIPTIONS[route],
                "intents": intents,
            }
        )

    @mcp_tool(
        name="get_clarification_questions",
        description=(
            "Returns a structured disambiguation script for a specific ambiguous area. "
            "Use this when the customer's utterance could map to multiple intents and "
            "you need to ask a follow-up question. "
            "Valid area values: tech_vs_nontech, which_service, router_type, "
            "internet_fault_type, wifi_vs_internet, mobile_issue_type, "
            "service_change_vs_fault, voyo_vs_tv. "
            "Returns: the question to ask, LLM instructions, and a decision tree "
            "mapping answer patterns to intent hints and optional next disambiguation steps."
        ),
        registry=registry,
    )
    def get_clarification_questions(area: str) -> str:
        area = area.strip()
        script = CLARIFICATION_SCRIPTS.get(area)
        if script is None:
            return _json(
                {
                    "error": f"Unknown disambiguation area: '{area}'",
                    "available_areas": list(CLARIFICATION_SCRIPTS.keys()),
                }
            )
        return _json(script)

    @mcp_tool(
        name="resolve_intent",
        description=(
            "Validates and formats the final intent classification. "
            "Call this when you have determined the intent_id and are ready to return "
            "the result to the caller. "
            "Parameters: "
            "  utterance — the original customer utterance (raw ASR). "
            "  intent_id — the intent code you determined (e.g. INTERNET_NO_SERVICE). "
            "  summary — one sentence in Slovak describing what the customer needs (max 120 chars). "
            "  confidence — your confidence as a decimal 0.0–1.0 (e.g. '0.92'). "
            "  follow_up_question — a clarifying question if still needed, or empty string. "
            "The tool enforces: correct route derived from catalog, special priority overrides, "
            "fallback to NONSPEC_ISSUE for unknown/empty intent_id. "
            "Returns the canonical JSON: {route, intent_id, confidence, summary, follow_up_question}."
        ),
        registry=registry,
    )
    def resolve_intent(
        utterance: str,
        intent_id: str,
        summary: str,
        confidence: str,
        follow_up_question: str,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        # 1. Special priority override — check utterance regardless of LLM's choice
        override = _check_special_priority(utterance)
        if override:
            _maybe_update_nlp_state(utterance, override, _meta)
            return _json(override)

        # 2. Parse confidence
        try:
            conf_float = float(confidence)
            conf_float = max(0.0, min(1.0, conf_float))
        except (ValueError, TypeError):
            conf_float = 0.5

        # 3. Normalize intent_id
        intent_id = (intent_id or "").strip().upper()

        # 4. Fallback for unknown / empty intent
        if not intent_id or intent_id not in INTENT_CATALOG:
            result = {
                "route": "standard_contact_center",
                "intent_id": "NONSPEC_ISSUE",
                "confidence": 0.30,
                "summary": summary or "Zákazníkov zámer nie je jasný",
                "follow_up_question": follow_up_question
                or "Prosím upresnite, s čím konkrétne potrebujete pomôcť?",
            }
            _maybe_update_nlp_state(utterance, result, _meta)
            return _json(result)

        # 5. Derive route from catalog (source of truth — never trust LLM's route)
        route = INTENT_ROUTE[intent_id]

        # 6. Build result
        result = {
            "route": route,
            "intent_id": intent_id,
            "confidence": round(conf_float, 2),
            "summary": (summary or "").strip()[:120],
            "follow_up_question": (follow_up_question or "").strip(),
        }

        # 7. Update NLP state
        _maybe_update_nlp_state(utterance, result, _meta)

        return _json(result)
