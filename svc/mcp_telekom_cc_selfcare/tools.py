"""Telekom CC Selfcare MCP tools.

authentication(phone_number?, kod_adresata?, rodne_cislo_last4?)
— Two-step customer authentication: find customer, then verify birth number last 4 digits.

resend_invoice(confirmed?)
— Resend invoice to registered email. Requires authentication first.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

from .customer_db import find_by_id, find_by_kod_adresata, find_by_phone

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


def _mask_email(email: str) -> str:
    local, domain = email.split("@", 1)
    return local[0] + "***@" + domain


# Per-conversation authentication state (in-memory, not persistent)
_AUTH_STATE: dict[str, dict[str, Any]] = {}
_MAX_VERIFICATION_ATTEMPTS = 3


def register(registry: ToolRegistry) -> None:

    @mcp_tool(
        name="authentication",
        description=(
            "Authenticate the customer. Two-step process:\n"
            "Step 1 — Find customer: provide phone_number (from caller ID, NEVER ask customer) "
            "or kod_adresata (customer reads from invoice). Tool returns 'verification_required'.\n"
            "Step 2 — Verify: call again with rodne_cislo_last4 (last 4 digits of birth number "
            "that the customer provides). Tool returns 'authenticated: true/false'.\n"
            "SECURITY: phone_number comes from SYSTEM only. Never ask customer for phone number. "
            "Never communicate or return full birth number — only ask for last 4 digits."
        ),
        registry=registry,
    )
    def authentication(
        phone_number: str | None = None,
        kod_adresata: str | None = None,
        rodne_cislo_last4: str | None = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conversation_id = (_meta or {}).get("conversation_id", "")
        state = _AUTH_STATE.get(conversation_id, {})

        # Step B — Verify birth number (pending_verification exists + last4 provided)
        pending = state.get("pending_verification")
        if pending and rodne_cislo_last4:
            last4 = rodne_cislo_last4.strip()
            expected = pending["expected_last4"]
            attempts = pending.get("attempts", 0) + 1

            if last4 == expected:
                _AUTH_STATE[conversation_id] = {
                    "authenticated": True,
                    "customer_id": pending["customer_id"],
                    "pending_verification": None,
                }
                return _json(
                    {
                        "authenticated": True,
                        "suggested_response": "Overenie prebehlo úspešne. Poslednú faktúru zašlem na Vašu fakturačnú e-mailovú adresu. Môžem pokračovať?",
                        "instruction": "Zavolaj resend_invoice() — tool vráti maskovanú e-mailovú adresu a požiada o potvrdenie zákazníka pred odoslaním.",
                    }
                )
            if attempts >= _MAX_VERIFICATION_ATTEMPTS:
                _AUTH_STATE.pop(conversation_id, None)
                return _json(
                    {
                        "authenticated": False,
                        "error": "max_attempts",
                        "suggested_response": "Overenie sa nepodarilo. Prepájam Vás na operátora.",
                        "instruction": "Zavolaj handover_to_human so zhrnutím situácie, skill='business'.",
                    }
                )
            pending["attempts"] = attempts
            remaining = _MAX_VERIFICATION_ATTEMPTS - attempts
            return _json(
                {
                    "authenticated": False,
                    "error": "wrong_digits",
                    "attempts_remaining": remaining,
                    "suggested_response": (
                        f"Zadané číslice sa nezhodujú. Skúste to, prosím, znova. "
                        f"Zostáva vám {'ešte jeden pokus' if remaining == 1 else f'{remaining} pokusy'}."
                    ),
                    "instruction": "Počkaj na zákazníka a zavolaj authentication znova s rodne_cislo_last4.",
                }
            )

        # Step A — Find customer
        customer = None
        method = None

        if phone_number:
            customer = find_by_phone(phone_number)
            method = "phone"

        if not customer and kod_adresata:
            customer = find_by_kod_adresata(kod_adresata)
            method = "kod_adresata"

        if not customer:
            if phone_number and not kod_adresata:
                return _json(
                    {
                        "status": "kod_adresata_required",
                        "suggested_response": "Váš účet som nenašla. Môžete mi poskytnúť Kód adresáta z faktúry?",
                        "instruction": "Počkaj na odpoveď a zavolaj authentication znova s kod_adresata=<odpoveď zákazníka>.",
                    }
                )
            return _json(
                {
                    "status": "not_found",
                    "suggested_response": "Zadaný kód som nenašla. Môžete ho skúsiť zadať znova?",
                    "instruction": (
                        "Počkaj na odpoveď a zavolaj authentication znova. "
                        "Ak ani druhý pokus neuspeje, informuj zákazníka a ukonči hovor."
                    ),
                }
            )

        # Store pending verification
        rc = customer.get("rodne_cislo", "")
        _AUTH_STATE[conversation_id] = {
            "authenticated": False,
            "customer_id": customer["id"],
            "pending_verification": {
                "customer_id": customer["id"],
                "expected_last4": rc[-4:] if len(rc) >= 4 else "",
                "attempts": 0,
            },
        }

        return _json(
            {
                "status": "verification_required",
                "method": method,
                "suggested_response": "Nájdla som Váš účet. Pre overenie totožnosti mi, prosím, povedzte posledné 4 číslice Vášho rodného čísla.",
                "instruction": "Počkaj na odpoveď a zavolaj authentication znova s rodne_cislo_last4=<odpoveď zákazníka>.",
            }
        )

    @mcp_tool(
        name="resend_invoice",
        description=(
            "Resend the customer's invoice to their registered email. "
            "Requires authentication first — call authentication() if not yet done. "
            "If customer has email and eBill enabled, requires explicit confirmation. "
            "Pass confirmed='true' after customer confirms to actually send."
        ),
        registry=registry,
    )
    def resend_invoice(
        confirmed: str | None = None,
        _meta: dict[str, Any] | None = None,
    ) -> str:
        conversation_id = (_meta or {}).get("conversation_id", "")
        auth = _AUTH_STATE.get(conversation_id, {})

        if not auth.get("authenticated"):
            return _json(
                {
                    "success": False,
                    "error": "not_authenticated",
                    "message": "Zákazník nie je overený. Najprv zavolajte authentication().",
                }
            )

        customer = find_by_id(auth["customer_id"])
        if not customer:
            return _json(
                {
                    "success": False,
                    "error": "customer_not_found",
                    "message": "Zákazník nebol nájdený v databáze.",
                }
            )

        if not customer.get("email"):
            return _json(
                {
                    "success": False,
                    "error": "no_email",
                    "suggested_response": "Bohužiaľ, na Vašom účte nie je evidovaný e-mail, faktúru nemôžem zaslať elektronicky. Prepájam Vás na operátora.",
                    "instruction": "Zavolaj handover_to_human so zhrnutím, skill='business'.",
                }
            )

        if not customer.get("ebill_enabled"):
            return _json(
                {
                    "success": False,
                    "error": "ebill_disabled",
                    "suggested_response": "Elektronická faktúra na Vašom účte nie je aktivovaná, preto ju nemôžem zaslať e-mailom. Prepájam Vás na operátora, ktorý vám s aktiváciou pomôže.",
                    "instruction": "Zavolaj handover_to_human so zhrnutím, skill='business'.",
                }
            )

        email_masked = _mask_email(customer["email"])

        if not confirmed or confirmed.strip().lower() not in ("true", "yes", "ano"):
            return _json(
                {
                    "success": False,
                    "status": "confirmation_required",
                    "email": email_masked,
                    "suggested_response": f"Faktúru pošlem na {email_masked}. Môžem pokračovať?",
                    "instruction": "Ak zákazník súhlasí, zavolaj resend_invoice znova s confirmed='true'.",
                }
            )

        # Mock send — confirmed
        if conversation_id:
            _nlp_set_state(conversation_id, {"invoice_resent": "true"})

        return _json(
            {
                "success": True,
                "email": email_masked,
                "suggested_response": f"Faktúra bola úspešne odoslaná na {email_masked}. Je ešte niečo, s čím vám môžem pomôcť?",
            }
        )
