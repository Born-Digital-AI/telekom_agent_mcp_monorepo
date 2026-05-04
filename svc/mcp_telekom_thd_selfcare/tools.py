"""Telekom THD Selfcare MCP tools.

find_service_point(phone_number?, kod_adresata?)
— Find customer's fixed internet service point.

get_info_router()
— Retrieve router model for the service point.

get_troubleshooting_steps(channel, step_result?)
— Two-phase: diagnose problem type, then step-by-step troubleshooting.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

from .customer_db import (
    find_by_kod_adresata,
    find_by_phone,
    get_fixed_internet_service,
)
from .troubleshooting_data import (
    DIAGNOSTIC_INSTRUCTION,
    DIAGNOSTIC_OPTIONS,
    DIAGNOSTIC_QUESTION,
    STEPS_BY_PROBLEM,
    get_step,
    match_problem_type,
)

_NLP_BASE_URL = os.environ.get("GOODBOT_URL", "http://goodbot.internal-test.svc.cluster.local:8121")
_log = logging.getLogger(__name__)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _nlp_set_state(conversation_id: str, named_entities: dict[str, Any]) -> None:
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
            _log.info("NLP state PUT %s -> HTTP %s body=%s", url, resp.status, resp_body)
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8", errors="replace")
        _log.warning("NLP state PUT %s -> HTTP %s body=%s", url, exc.code, resp_body)
    except Exception as exc:
        _log.warning("NLP state PUT %s -> error: %s", url, exc)


# Per-conversation session state (in-memory)
_SESSION_STATE: dict[str, dict[str, Any]] = {}


def _get_state(conversation_id: str) -> dict[str, Any]:
    if conversation_id not in _SESSION_STATE:
        _SESSION_STATE[conversation_id] = {
            "customer_id": None,
            "service_point": None,
            "router_model": None,
            "phone_number": None,
            "channel": None,
            "problem_type": None,
            "current_step_index": 0,
            "completed_steps": [],
        }
    return _SESSION_STATE[conversation_id]


def register(registry: ToolRegistry) -> None:

    @mcp_tool(
        name="find_service_point",
        description=(
            "Find the customer's fixed internet service point by phone number or Kód adresáta. "
            "phone_number comes from the SYSTEM (caller ID) — never ask the customer for it. "
            "If phone_number is not found, ask the customer for their Kód adresáta from the invoice. "
            "At least one of phone_number or kod_adresata must be provided. "
            "Returns the service address — confirm with the customer that this is their address."
        ),
        registry=registry,
    )
    def find_service_point(
        phone_number: str | None = None,
        kod_adresata: str | None = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conversation_id = (_meta or {}).get("conversation_id", "")

        customer = None
        if phone_number:
            customer = find_by_phone(phone_number)
        if not customer and kod_adresata:
            customer = find_by_kod_adresata(kod_adresata)

        if not customer:
            if not phone_number and not kod_adresata:
                return _json(
                    {
                        "status": "input_required",
                        "suggested_response": "Môžete mi poskytnúť Kód adresáta z Vašej faktúry?",
                        "instruction": "Počkaj na odpoveď zákazníka a zavolaj find_service_point znova s kod_adresata=<odpoveď zákazníka>.",
                    }
                )
            if phone_number and not kod_adresata:
                return _json(
                    {
                        "found": False,
                        "error": "not_found_by_phone",
                        "suggested_response": "Váš účet som nenašla podľa telefónneho čísla. Môžete mi poskytnúť Kód adresáta z faktúry?",
                        "instruction": "Počkaj na odpoveď a zavolaj find_service_point znova s kod_adresata=<odpoveď zákazníka>.",
                    }
                )
            return _json(
                {
                    "found": False,
                    "error": "not_found",
                    "suggested_response": "Zadaný kód som nenašla. Môžete ho skúsiť zadať znova?",
                    "instruction": (
                        "Počkaj na odpoveď a zavolaj find_service_point znova. "
                        "Ak ani druhý pokus neuspeje, odovzdaj zákazníka operátorovi."
                    ),
                }
            )

        fixed_svc = get_fixed_internet_service(customer)
        if not fixed_svc:
            return _json(
                {
                    "found": False,
                    "error": "no_fixed_internet",
                    "suggested_response": "Na Vašom účte neevidujem pevný internet. Prepájam Vás na operátora.",
                    "suggested_action": "handover_to_human",
                    "instruction": "Zavolaj handover_to_human so zhrnutím, skill='technical'.",
                }
            )

        router_model = fixed_svc.get("router_model")

        state = _get_state(conversation_id)
        state["customer_id"] = customer["id"]
        state["phone_number"] = phone_number
        state["service_point"] = {
            "address": fixed_svc["address"],
            "service_point_id": fixed_svc["service_point_id"],
        }
        state["router_model"] = router_model

        if conversation_id:
            _nlp_set_state(
                conversation_id,
                {
                    "service_point_id": fixed_svc["service_point_id"],
                },
            )

        if not router_model:
            return _json(
                {
                    "found": True,
                    "address": fixed_svc["address"],
                    "router_model": None,
                    "error": "unknown_router",
                    "suggested_response": f"Našla som Vašu adresu: {fixed_svc['address']}. Model Vášho routera však nemám v systéme evidovaný. Prepájam Vás na technickú podporu.",
                    "suggested_action": "handover_to_human",
                    "instruction": "Zavolaj handover_to_human so zhrnutím, skill='technical'.",
                }
            )

        return _json(
            {
                "found": True,
                "address": fixed_svc["address"],
                "router_model": router_model,
                "suggested_response": f"Našla som Vašu adresu: {fixed_svc['address']}. Je to správna adresa?",
                "instruction": "Ak zákazník potvrdí adresu, zavolaj get_troubleshooting_steps().",
            }
        )

    @mcp_tool(
        name="get_info_router",
        description=(
            "Retrieve the router model for the customer's service point. "
            "Requires find_service_point() to have been called first. "
            "Returns router model, which is needed before get_troubleshooting_steps()."
        ),
        registry=registry,
    )
    def get_info_router(
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conversation_id = (_meta or {}).get("conversation_id", "")
        state = _get_state(conversation_id)

        if not state.get("service_point"):
            return _json(
                {
                    "success": False,
                    "error": "no_service_point",
                    "message": "Najprv zavolajte find_service_point().",
                }
            )

        router_model = state.get("router_model")
        if not router_model:
            return _json(
                {
                    "success": False,
                    "error": "unknown_router",
                    "suggested_response": "Model Vášho routera nemám v systéme evidovaný. Prepájam Vás na technickú podporu.",
                    "suggested_action": "handover_to_human",
                    "instruction": "Zavolaj handover_to_human so zhrnutím, skill='technical'.",
                }
            )

        return _json(
            {
                "success": True,
                "router_model": router_model,
            }
        )

    @mcp_tool(
        name="get_troubleshooting_steps",
        description=(
            "Get WiFi/internet troubleshooting steps. Two phases:\n"
            "Phase 1 (diagnosis): Returns a question to identify the problem type. "
            "Call again with step_result containing the customer's answer.\n"
            "Phase 2 (troubleshooting): Returns the next step. After the customer tries it, "
            "call again with step_result='resolved', 'not_resolved', or 'skipped'.\n"
            "channel: 'call' (voice — simple spoken instructions) or 'chat' (markdown with images). "
            "Set channel on first call, tool remembers it.\n"
            "step_result: customer's answer (phase 1) or step outcome (phase 2)."
        ),
        registry=registry,
    )
    def get_troubleshooting_steps(
        channel: str,
        step_result: str | None = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conversation_id = (_meta or {}).get("conversation_id", "")
        state = _get_state(conversation_id)
        router_model = state.get("router_model")

        if not router_model:
            return _json(
                {
                    "success": False,
                    "error": "no_router_info",
                    "message": "Najprv zavolajte get_info_router().",
                }
            )

        # Set channel on first call, remember for subsequent; default to chat
        ch = channel.strip().lower() if channel else ""
        if ch in ("call", "chat"):
            state["channel"] = ch
        elif state.get("channel"):
            ch = state["channel"]
        else:
            ch = "chat"
            state["channel"] = ch

        # ── Phase 1: Diagnosis ──
        if not state.get("problem_type"):
            if step_result:
                matched = match_problem_type(step_result)
                if matched:
                    state["problem_type"] = matched
                    state["current_step_index"] = 0
                    state["completed_steps"] = []
                    # Fall through to Phase 2
                else:
                    return _json(
                        {
                            "phase": "diagnosis",
                            "error": "unrecognized_answer",
                            "question": DIAGNOSTIC_QUESTION,
                            "options": DIAGNOSTIC_OPTIONS,
                            "instruction": (
                                "Odpoveď zákazníka nebola rozpoznaná. "
                                "Skúste sa opýtať znova alebo vyberte z možností."
                            ),
                        }
                    )
            else:
                return _json(
                    {
                        "phase": "diagnosis",
                        "question": DIAGNOSTIC_QUESTION,
                        "options": DIAGNOSTIC_OPTIONS,
                        "instruction": DIAGNOSTIC_INSTRUCTION,
                    }
                )

        # ── Phase 2: Step-by-step troubleshooting ──
        _VALID_STEP_RESULTS = {"resolved", "not_resolved", "skipped"}

        problem_type = state["problem_type"]
        step_ids = STEPS_BY_PROBLEM.get(problem_type, [])

        # Process previous step result
        if step_result and state["completed_steps"] is not None and state["current_step_index"] > 0:
            result_lower = step_result.strip().lower()

            if result_lower not in _VALID_STEP_RESULTS:
                return _json(
                    {
                        "error": "invalid_step_result",
                        "valid_values": sorted(_VALID_STEP_RESULTS),
                        "instruction": (
                            "Opýtaj sa zákazníka, či krok pomohol. "
                            "Zavolaj tool znova s step_result='resolved', 'not_resolved' alebo 'skipped'."
                        ),
                    }
                )

            prev_idx = state["current_step_index"] - 1
            if prev_idx < len(step_ids):
                prev_step_id = step_ids[prev_idx]

                if result_lower == "resolved":
                    state["completed_steps"].append({"step_id": prev_step_id, "result": "resolved"})
                    if conversation_id:
                        _nlp_set_state(conversation_id, {"troubleshooting_result": "resolved"})
                    return _json(
                        {
                            "phase": "resolved",
                            "suggested_response": "Výborne! Teším sa, že sme to vyriešili. Ak by ste potrebovali ďalšiu pomoc, neváhajte zavolať.",
                        }
                    )
                if result_lower == "skipped":
                    state["completed_steps"].append({"step_id": prev_step_id, "result": "skipped"})
                    if conversation_id:
                        _nlp_set_state(
                            conversation_id,
                            {
                                "troubleshooting_last_step": prev_step_id,
                                "troubleshooting_last_result": "skipped",
                            },
                        )
                else:
                    state["completed_steps"].append(
                        {"step_id": prev_step_id, "result": "not_resolved"}
                    )
                    if conversation_id:
                        _nlp_set_state(
                            conversation_id,
                            {
                                "troubleshooting_last_step": prev_step_id,
                                "troubleshooting_last_result": "not_resolved",
                            },
                        )

        # Get next step
        idx = state["current_step_index"]
        if idx >= len(step_ids):
            return _json(
                {
                    "phase": "escalate",
                    "suggested_response": "Vyskúšali sme všetky kroky, ale problém pretrváva. Prepájam Vás na technickú podporu.",
                    "suggested_action": "handover_to_human",
                    "instruction": "Zavolaj handover_to_human so zhrnutím vyskúšaných krokov, skill='technical'.",
                }
            )

        current_step_id = step_ids[idx]
        state["current_step_index"] = idx + 1

        step_data = get_step(current_step_id, ch, router_model)
        step_data["step_number"] = idx + 1
        step_data["total_steps"] = len(step_ids)

        response: dict[str, Any] = {
            "phase": "troubleshooting",
            "problem_type": problem_type,
            "step": step_data,
        }

        # Offer SMS for call channel on complex steps
        if ch == "call" and current_step_id in ("channel_change", "check_service_mode"):
            phone = state.get("phone_number")
            response["sms_offer"] = {
                "available": bool(phone),
                "phone_number": phone,
                "message": "Môžem vám zaslať SMS s odkazom na podrobný postup.",
                "sms_link": "https://www.telekom.sk/wiki/internet/nefunguje-mi-wi-fi",
            }

        return _json(response)
