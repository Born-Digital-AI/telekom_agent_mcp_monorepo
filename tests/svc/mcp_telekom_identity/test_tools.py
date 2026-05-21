"""Tests for the identifikacia_rodne_cislo tool."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from lib.mcp_service.legacy_compat import ToolRegistry
from svc.mcp_telekom_identity import tools as identity_tools


class _FakeMCP:
    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(self, *, name: str, description: str | None = None):  # noqa: ARG002
        def decorator(fn):
            self.registered[name] = fn
            return fn

        return decorator


class _StubClient:
    """A stub DPSGetClient that returns canned responses and records calls."""

    def __init__(
        self,
        parties: list[dict] | Exception | None = None,
        customers_by_party: dict[str, list[dict]] | Exception | None = None,
    ) -> None:
        self.parties = parties if parties is not None else []
        self.customers_by_party = customers_by_party if customers_by_party is not None else {}
        self.party_calls: list[tuple[str, str]] = []
        self.customer_calls: list[str] = []

    async def get_parties_by_identification(
        self,
        identification_id: str,
        identification_type: str,
    ) -> list[dict]:
        self.party_calls.append((identification_id, identification_type))
        if isinstance(self.parties, Exception):
            raise self.parties
        return self.parties

    async def get_customers_by_engaged_party(self, party_id: str) -> list[dict]:
        self.customer_calls.append(party_id)
        if isinstance(self.customers_by_party, Exception):
            raise self.customers_by_party
        return self.customers_by_party.get(party_id, [])


@pytest.fixture
def make_tool():
    """Build the tool against a stub client; return (tool_fn, client_stub) factory."""

    def _factory(
        parties=None,
        customers_by_party=None,
        max_candidates: int = 10,
    ):
        stub = _StubClient(parties=parties, customers_by_party=customers_by_party)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_rodne_cislo"], stub

    return _factory


@pytest.fixture
def conv():
    token_c = current_conversation_id.set("conv-test")
    token_i = current_interaction_id.set("inter-test")
    try:
        yield
    finally:
        current_conversation_id.reset(token_c)
        current_interaction_id.reset(token_i)


async def _call(tool, **kwargs) -> dict:
    return json.loads(await tool(**kwargs))


@pytest.mark.unit
async def test_rejects_empty_input(make_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_tool()
    result = await _call(tool, rodne_cislo="")
    assert result == {
        "found": False,
        "error": "invalid_input",
        "message": "Rodné číslo musí mať 9 alebo 10 cifier (bez lomky).",
    }
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["abc", "12345678", "123456789012", "12345/6789", " "])
async def test_rejects_non_digit_or_wrong_length(make_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_tool()
    result = await _call(tool, rodne_cislo=bad)
    assert result["found"] is False
    assert result["error"] == "invalid_input"
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("good", ["123456789", "8753189467"])
async def test_valid_format_reaches_party_call(make_tool, conv, good) -> None:  # noqa: ARG001
    tool, stub = make_tool(parties=[])
    await _call(tool, rodne_cislo=good)
    assert stub.party_calls == [(good, "socialSecurityNumber")]
