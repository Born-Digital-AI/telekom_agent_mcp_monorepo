"""Telekom Main Triage MCP tools.

list_selfcare_processes()
— Returns catalog of selfcare processes the bot can handle.

switch_to_selfcare(target_process)
— Initiates a selfcare process. Accepted: resend_invoice, internet_issues.

handover_to_human(summary, skill)
— Transfers customer to human operator with summary and skill routing.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

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


_VALID_SELFCARE_PROCESSES = {"resend_invoice", "internet_issues"}


def register(registry: ToolRegistry) -> None:

    @mcp_tool(
        name="list_selfcare_processes",
        description=(
            "Returns a catalog of selfcare processes the bot can handle autonomously. "
            "Call this to understand what is possible before routing the customer. "
            "No input parameters required."
        ),
        registry=registry,
    )
    def list_selfcare_processes() -> str:
        return _json(
            {
                "processes": {
                    "resend_invoice": {
                        "label": "Opätovné zaslanie faktúry",
                        "can_do": "Zaslať faktúru na registrovaný e-mail zákazníka.",
                        "cannot_do": "Zmeniť e-mailovú adresu ani spôsob doručovania faktúry.",
                        "ambiguous_trigger": (
                            "Zákazník hovorí všeobecne o faktúre — spýtajte sa, "
                            "či chce faktúru opätovne zaslať, alebo niečo iné."
                        ),
                    },
                    "internet_issues": {
                        "label": "Porucha internetu / WiFi",
                        "can_do": (
                            "Diagnostikovať a riešiť nefunkčný pevný internet alebo WiFi. "
                            "Krok po kroku previesť zákazníka cez troubleshooting."
                        ),
                        "cannot_do": ("Mobilný internet, výmenu routera ani problémy s TV."),
                        "ambiguous_trigger": (
                            "Zákazník hovorí 'nefunguje mi' bez upresnenia — "
                            "spýtajte sa, čo konkrétne nefunguje."
                        ),
                    },
                },
                "note": "Ak zámer zákazníka nezodpovedá žiadnemu procesu, použite handover_to_human.",
            }
        )

    @mcp_tool(
        name="switch_to_selfcare",
        description=(
            "Initiates a selfcare process for the customer. "
            "Accepted values for target_process: 'resend_invoice', 'internet_issues'. "
            "Any other value will be rejected — use handover_to_human for unsupported processes."
        ),
        registry=registry,
    )
    def switch_to_selfcare(
        target_process: str,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        target = target_process.strip().lower()
        if target not in _VALID_SELFCARE_PROCESSES:
            return _json(
                {
                    "success": False,
                    "error": "process_not_available",
                    "suggested_action": "handover_to_human",
                    "instruction": "Zavolaj handover_to_human so zhrnutím problému zákazníka.",
                    "suggested_response": "S tým vám bohužiaľ cez túto linku nepomôžem, prepájam vás na kolegu.",
                }
            )

        if _meta:
            conversation_id = _meta.get("conversation_id")
            if conversation_id:
                _nlp_set_state(conversation_id, {"target_process": target})

        _responses = {
            "resend_invoice": "Dobre, overím váš účet a zašlem faktúru.",
            "internet_issues": "Dobre, pomôžem vám s internetom. Hneď sa do toho pustíme.",
        }
        return _json(
            {
                "success": True,
                "target_process": target,
                "suggested_response": _responses[target],
                "instruction": (
                    "Použi suggested_response a nevykonaj žiadne ďalšie akcie ani otázky. "
                    "Systém automaticky prepne na správny proces."
                ),
            }
        )

    @mcp_tool(
        name="handover_to_human",
        description=(
            "Transfer the customer to a human operator. "
            "Use skill='technical' for: internet/wifi problems, router issues, TV signal, "
            "service outages, slow internet, mobile data problems, technical diagnostics. "
            "Use skill='business' for: billing questions, contract changes, plan upgrades, "
            "cancellations, porting, SIM issues, complaints, payments, account changes. "
            "IMPORTANT: Only call this after gathering at least a one-sentence description "
            "of the customer's problem. Never call without basic clarification first."
        ),
        registry=registry,
    )
    def handover_to_human(
        summary: str,
        skill: str,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        skill_normalized = skill.strip().lower()
        if skill_normalized not in ("technical", "business"):
            return _json(
                {
                    "success": False,
                    "error": "invalid_skill",
                    "message": "Hodnota skill musí byť 'technical' alebo 'business'.",
                }
            )

        summary_clean = summary.strip()
        if not summary_clean:
            return _json(
                {
                    "success": False,
                    "error": "empty_summary",
                    "message": "Poskytnite krátky popis problému zákazníka.",
                }
            )

        if _meta:
            conversation_id = _meta.get("conversation_id")
            if conversation_id:
                _nlp_set_state(
                    conversation_id,
                    {
                        "handover_summary": summary_clean,
                        "handover_skill": skill_normalized,
                    },
                )

        _skill_labels = {
            "technical": "technického špecialistu",
            "business": "operátora",
        }
        return _json(
            {
                "success": True,
                "handover_skill": skill_normalized,
                "handover_summary": summary_clean,
                "suggested_response": f"Prepájam Vás na {_skill_labels[skill_normalized]}, ktorý vám pomôže. Chvíľku strpenia.",
            }
        )
