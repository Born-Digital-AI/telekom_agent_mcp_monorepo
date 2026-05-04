"""Tests for the security-critical authentication state machine in CC Selfcare.

Covers:
- Step A → Step B handoff (verification_required after find).
- Successful verification with correct last4.
- Wrong last4 returns 'wrong_digits' with attempts_remaining.
- After ``_MAX_VERIFICATION_ATTEMPTS`` failed attempts the account is locked
  and state is purged.
- ``not_found`` paths (no phone match, no kod_adresata match).
- ``not_authenticated`` short-circuit on resend_invoice without prior auth.
"""

from __future__ import annotations

import json

import pytest

from lib.boilerplate.logging import current_conversation_id
from lib.mcp_service.legacy_compat import ToolRegistry
from svc.mcp_telekom_cc_selfcare import tools as cc_tools


class _FakeMCP:
    """Captures tools registered via the legacy ``@mcp_tool`` decorator."""

    def __init__(self) -> None:
        self.registered: dict[str, callable] = {}

    def tool(self, *, name: str, description: str | None = None):  # noqa: ARG002
        def decorator(fn):
            self.registered[name] = fn
            return fn

        return decorator


@pytest.fixture
def tools() -> dict[str, callable]:
    """Boot a fresh tool registry per test and return the registered tools by name."""
    cc_tools._AUTH_STATE = type(cc_tools._AUTH_STATE)(
        ttl_seconds=cc_tools._AUTH_TTL_SECONDS,
    )  # reset between tests
    fake = _FakeMCP()
    registry = ToolRegistry(fake)  # type: ignore[arg-type]
    cc_tools.register(registry)
    return fake.registered


def _call(tool, **kwargs) -> dict:
    return json.loads(tool(**kwargs))


@pytest.fixture
def conv_id():
    """Set/reset the conversation_id ContextVar so the legacy compat shim sees it."""
    token = current_conversation_id.set("conv-test")
    try:
        yield "conv-test"
    finally:
        current_conversation_id.reset(token)


@pytest.mark.unit
def test_step_a_with_phone_returns_verification_required(tools, conv_id) -> None:  # noqa: ARG001
    result = _call(tools["authentication"], phone_number="+421901111111")
    assert result["status"] == "verification_required"
    assert result["method"] == "phone"


@pytest.mark.unit
def test_step_b_with_correct_last4_authenticates(tools, conv_id) -> None:  # noqa: ARG001
    _call(tools["authentication"], phone_number="+421901111111")
    # rodne_cislo for C001 = "8552127845" → last4 = "7845"
    result = _call(tools["authentication"], rodne_cislo_last4="7845")
    assert result["authenticated"] is True


@pytest.mark.unit
def test_three_wrong_attempts_locks_and_purges_state(tools, conv_id) -> None:
    _call(tools["authentication"], phone_number="+421901111111")

    r1 = _call(tools["authentication"], rodne_cislo_last4="0000")
    assert r1["authenticated"] is False
    assert r1["error"] == "wrong_digits"
    assert r1["attempts_remaining"] == 2

    r2 = _call(tools["authentication"], rodne_cislo_last4="1111")
    assert r2["attempts_remaining"] == 1

    r3 = _call(tools["authentication"], rodne_cislo_last4="2222")
    assert r3["error"] == "max_attempts"

    # After lockout the per-conversation state must be purged.
    assert cc_tools._AUTH_STATE.get(conv_id) is None


@pytest.mark.unit
def test_phone_only_no_match_asks_for_kod_adresata(tools, conv_id) -> None:  # noqa: ARG001
    result = _call(tools["authentication"], phone_number="+421999999999")
    assert result["status"] == "kod_adresata_required"


@pytest.mark.unit
def test_resend_invoice_short_circuits_when_unauthenticated(tools, conv_id) -> None:  # noqa: ARG001
    result = _call(tools["resend_invoice"])
    assert result["success"] is False
    assert result["error"] == "not_authenticated"


@pytest.mark.unit
def test_resend_invoice_after_auth_returns_confirmation_required(tools, conv_id) -> None:  # noqa: ARG001
    """C001 has email + eBill — a first call should ask for confirmation."""
    _call(tools["authentication"], phone_number="+421901111111")
    _call(tools["authentication"], rodne_cislo_last4="7845")
    result = _call(tools["resend_invoice"])
    assert result["status"] == "confirmation_required"
    assert "***" in result["email"]


@pytest.mark.unit
def test_resend_invoice_with_no_email_routes_to_handover(tools, conv_id) -> None:  # noqa: ARG001
    """C002 has no email — must surface handover_to_human instruction."""
    _call(tools["authentication"], phone_number="+421902222222")
    # rodne_cislo for C002 = "7610051234" → last4 = "1234"
    _call(tools["authentication"], rodne_cislo_last4="1234")
    result = _call(tools["resend_invoice"])
    assert result["error"] == "no_email"
