"""Tests for the identifikacia_rodne_cislo tool."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from lib.mcp_service.legacy_compat import ToolRegistry
from svc.mcp_telekom_identity import tools as identity_tools
from svc.mcp_telekom_identity.dps_get_client import (
    DPSAuthError,
    DPSInvalidResponseError,
    DPSNetworkError,
    DPSTimeoutError,
    DPSUpstreamError,
)

_UPSTREAM_MESSAGE = "Vyskytol sa technický problém. Prepojím vás na operátora."


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

    customer_by_id_calls: list[str]
    billing_account_by_id_calls: list[str]
    customer_by_id_map: dict[str, dict | None] | Exception
    billing_account_by_id_map: dict[str, dict | None] | Exception
    products_by_public_identifier_calls: list[str]
    products_by_public_identifier_map: dict[str, list[dict] | Exception] | Exception
    products_by_serial_number_calls: list[str]
    products_by_serial_number_map: dict[str, list[dict] | Exception] | Exception
    party_by_id_calls: list[str]
    party_by_id_map: dict[str, dict | None] | Exception

    def __init__(  # noqa: PLR0913
        self,
        parties: list[dict] | Exception | None = None,
        customers_by_party: dict[str, list[dict]] | Exception | None = None,
        customer_by_id_map: dict[str, dict | None] | Exception | None = None,
        billing_account_by_id_map: dict[str, dict | None] | Exception | None = None,
        products_by_public_identifier_map: dict[str, list[dict] | Exception]
        | Exception
        | None = None,
        products_by_serial_number_map: dict[str, list[dict] | Exception] | Exception | None = None,
        party_by_id_map: dict[str, dict | None] | Exception | None = None,
    ) -> None:
        self.parties = parties if parties is not None else []
        self.customers_by_party = customers_by_party if customers_by_party is not None else {}
        self.party_calls: list[tuple[str, str]] = []
        self.customer_calls: list[str] = []
        self.customer_by_id_calls = []
        self.billing_account_by_id_calls = []
        self.products_by_public_identifier_calls = []
        self.products_by_serial_number_calls = []
        self.party_by_id_calls = []
        self.customer_by_id_map = customer_by_id_map if customer_by_id_map is not None else {}
        self.billing_account_by_id_map = (
            billing_account_by_id_map if billing_account_by_id_map is not None else {}
        )
        self.products_by_public_identifier_map = (
            products_by_public_identifier_map
            if products_by_public_identifier_map is not None
            else {}
        )
        self.products_by_serial_number_map = (
            products_by_serial_number_map if products_by_serial_number_map is not None else {}
        )
        self.party_by_id_map = party_by_id_map if party_by_id_map is not None else {}

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

    async def get_customer_by_id(self, customer_id: str) -> dict | None:
        self.customer_by_id_calls.append(customer_id)
        if isinstance(self.customer_by_id_map, Exception):
            raise self.customer_by_id_map
        return self.customer_by_id_map.get(customer_id)

    async def get_billing_account_by_id(self, account_id: str) -> dict | None:
        self.billing_account_by_id_calls.append(account_id)
        if isinstance(self.billing_account_by_id_map, Exception):
            raise self.billing_account_by_id_map
        return self.billing_account_by_id_map.get(account_id)

    async def get_products_by_public_identifier(self, public_identifier: str) -> list[dict]:
        self.products_by_public_identifier_calls.append(public_identifier)
        if isinstance(self.products_by_public_identifier_map, Exception):
            raise self.products_by_public_identifier_map
        return self.products_by_public_identifier_map.get(public_identifier, [])

    async def get_products_by_serial_number(self, serial_number: str) -> list[dict]:
        self.products_by_serial_number_calls.append(serial_number)
        if isinstance(self.products_by_serial_number_map, Exception):
            raise self.products_by_serial_number_map
        val = self.products_by_serial_number_map.get(serial_number, [])
        if isinstance(val, Exception):
            raise val
        return val

    async def get_party_by_id(self, party_id: str) -> dict | None:
        self.party_by_id_calls.append(party_id)
        if isinstance(self.party_by_id_map, Exception):
            raise self.party_by_id_map
        return self.party_by_id_map.get(party_id)


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


@pytest.fixture(autouse=True)
def _reset_identity_state_and_silence_nlp(monkeypatch):
    from svc.mcp_telekom_identity import tools as identity_tools

    # Reset cache between tests
    identity_tools._IDENTITY_STATE = type(identity_tools._IDENTITY_STATE)(
        ttl_seconds=identity_tools._IDENTITY_TTL_SECONDS,
    )
    identity_tools._NLP_MIRROR_STATE = type(identity_tools._NLP_MIRROR_STATE)(
        ttl_seconds=identity_tools._NLP_MIRROR_TTL_SECONDS,
    )
    identity_tools._AUTH_STATE = type(identity_tools._AUTH_STATE)(
        ttl_seconds=identity_tools._AUTH_TTL_SECONDS,
    )

    # Silence + capture NLP pushes for assertions
    calls: list[tuple[str, dict]] = []

    def _capture(conversation_id, named_entities):
        calls.append((conversation_id, named_entities))
        # Still mirror into _NLP_MIRROR_STATE so tests that assert on mirror work.
        if conversation_id:
            current = identity_tools._NLP_MIRROR_STATE.get(conversation_id) or {}
            current.update({k: str(v) for k, v in named_entities.items()})
            identity_tools._NLP_MIRROR_STATE.set(conversation_id, current)

    monkeypatch.setattr(identity_tools, "_nlp_set_state", _capture)
    return calls  # tests that need the list can use the `_reset_identity_state_and_silence_nlp` fixture


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
        "message": "Rodné číslo nie je v správnom tvare. Zadajte ho ako 9 alebo 10 cifier bez lomky.",
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


def _party(
    party_id: str,
    status: str = "initialized",
    entity_type: str = "Party",
    name: tuple[str, str] = ("Tester", "AT NECHYTAT"),
) -> dict:
    return {
        "id": party_id,
        "status": status,
        "entityType": entity_type,
        "type": "individual",
        "individual": {
            "givenName": name[0],
            "familyName": name[1],
            "individualIdentifications": [],
        },
        "contacts": [],
    }


@pytest.mark.unit
async def test_not_found_when_no_party_matches(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=[])
    result = await _call(tool, rodne_cislo="8753189467")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Zákazníka s týmto rodným číslom sa nepodarilo nájsť.",
    }


@pytest.mark.unit
async def test_filters_contactparty_and_terminal_statuses(make_tool, conv) -> None:  # noqa: ARG001
    # P1: Party, initialized → accepted
    # P2: ContactParty, initialized → rejected (wrong entityType)
    # P3: Party, deceased → rejected (terminal)
    # P4: Party, validated → accepted
    parties = [
        _party("PARTY_1", status="initialized", name=("A", "A")),
        _party("PARTY_2", entity_type="ContactParty"),
        _party("PARTY_3", status="deceased"),
        _party("PARTY_4", status="validated", name=("B", "B")),
    ]
    tool, _stub = make_tool(parties=parties)
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    # 2 unique party_ids → multiple_matches
    assert result["multiple_matches"] is True
    assert "A A" in result["names"]
    assert "B B" in result["names"]


@pytest.mark.unit
async def test_filters_closed_status(make_tool, conv) -> None:  # noqa: ARG001
    parties = [
        _party("PARTY_1", status="closed"),
        _party("PARTY_2", name=("Jana", "Nováková")),
    ]
    tool, _stub = make_tool(parties=parties)
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    # Only PARTY_2 passes → single match
    assert result.get("multiple_matches") is None
    assert result["name"] == "Jana Nováková"


@pytest.mark.unit
async def test_accepts_party_with_null_status(make_tool, conv) -> None:  # noqa: ARG001
    party = _party("PARTY_NULL", name=("Org", "Test"))
    party["status"] = None  # mirror the org case
    tool, _ = make_tool(parties=[party])
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    assert result["name"] == "Org Test"


@pytest.mark.unit
async def test_caps_candidates_at_max_and_marks_truncated(make_tool, conv) -> None:  # noqa: ARG001
    parties = [_party(f"PARTY_{i}") for i in range(25)]
    tool, stub = make_tool(parties=parties, max_candidates=10)
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    assert result["multiple_matches"] is True
    assert len(result["names"]) >= 1
    assert len(stub.customer_calls) == 10
    # Cache holds the 10 capped candidates.
    cached = identity_tools._IDENTITY_STATE.get("conv-test")
    assert cached is not None
    assert len(cached["candidates"]) == 10


@pytest.mark.unit
async def test_dedup_by_party_id(make_tool, conv) -> None:  # noqa: ARG001
    parties = [_party("PARTY_1"), _party("PARTY_1"), _party("PARTY_2")]
    tool, _ = make_tool(parties=parties)
    result = await _call(tool, rodne_cislo="8753189467")
    # 2 unique party_ids → multiple_matches
    assert result["found"] is True
    assert result["multiple_matches"] is True


def _full_party(party_id: str = "PARTY_4482259100") -> dict:
    return {
        "id": party_id,
        "status": "initialized",
        "entityType": "Party",
        "type": "individual",
        "individual": {
            "givenName": "Tester",
            "familyName": "AT NECHYTAT",
            "individualIdentifications": [
                {
                    "identificationId": "MM852148",
                    "name": "IDNumber",
                    "type": "nationalIdentityCard",
                },
                {
                    "identificationId": "8753189467",
                    "name": "OIBNumber",
                    "type": "socialSecurityNumber",
                },
            ],
        },
        "contacts": [
            {"type": "mobile", "role": {"name": "main"}, "medium": {"number": "0902555002"}},
            {
                "type": "email",
                "role": {"name": "main"},
                "medium": {"emailAddress": "test@telekom.sk"},
            },
            {
                "type": "address",
                "role": {"name": "main"},
                "medium": {
                    "address": {
                        "streetName": "Hubeného",
                        "streetNr": "9",
                        "postcode": "83153",
                        "locality": "Rača",
                    },
                },
            },
        ],
    }


def _customer(customer_id: str = "4482259100", party_id: str = "PARTY_4482259100") -> dict:
    return {
        "id": customer_id,
        "name": "AT NECHYTAT,Tester",
        "status": "preactive",
        "marketSegment": "Basic",
        "customerSegment": "B2C",
        "validFor": {"startDateTime": "2026-02-01T00:00:00Z"},
        "characteristics": [
            {"name": "natcoClassType", "value": "Customer"},
            {"name": "treatmentPackage", "value": "Premium Basic"},
        ],
        "engagedParty": {"entityReferredType": "Party", "id": party_id},
    }


@pytest.mark.unit
async def test_single_match_merges_party_and_customer(make_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_party()
    customer = _customer()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result == {"found": True, "name": "Tester AT NECHYTAT"}
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cand] = cached["candidates"]
    assert cand["billing_account_ids"] == []  # _customer() has no customerAccounts
    assert cand["auth_rc_last4"] == "9467"  # last 4 of 8753189467


@pytest.mark.unit
async def test_party_with_no_customer_yields_candidate_with_null_customer_id(
    make_tool,
    conv,  # noqa: ARG001
) -> None:
    tool, _ = make_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": []},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    # Single party_id → single match shape with name from Party
    assert result == {"found": True, "name": "Tester AT NECHYTAT"}


@pytest.mark.unit
async def test_party_with_two_customers_yields_two_candidates_sharing_party_id(
    make_tool,
    conv,  # noqa: ARG001
) -> None:
    party = _full_party()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={
            "PARTY_4482259100": [
                _customer(customer_id="A1"),
                _customer(customer_id="A2"),
            ],
        },
    )
    result = await _call(tool, rodne_cislo="8753189467")
    # 1 unique party_id → single match externally
    assert result == {"found": True, "name": "Tester AT NECHYTAT"}
    # But cache holds both internal candidates
    cached = identity_tools._IDENTITY_STATE.get("conv-test")
    assert cached is not None
    assert len(cached["candidates"]) == 2
    assert {c["customer_id"] for c in cached["candidates"]} == {"A1", "A2"}


@pytest.mark.unit
async def test_address_formatting_handles_missing_pieces(make_tool, conv) -> None:  # noqa: ARG001
    party = _full_party()
    party["contacts"] = [
        {
            "type": "address",
            "role": {"name": "main"},
            "medium": {
                "address": {
                    "streetName": "Mierová",
                    # no streetNr
                    "postcode": "04001",
                    "locality": "Košice",
                },
            },
        },
    ]
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    # Response is minimal — verify address is in the cache
    assert result["found"] is True
    cached = identity_tools._IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cached_candidate] = cached["candidates"]
    assert {"type": "address", "value": "Mierová, 04001 Košice"} in cached_candidate["contacts"]


@pytest.mark.unit
async def test_auth_error_maps_to_auth_failed_json(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=DPSAuthError("bad token"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result == {
        "found": False,
        "error": "auth_failed",
        "message": _UPSTREAM_MESSAGE,
    }


@pytest.mark.unit
async def test_upstream_error_maps_to_upstream_error_json(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=DPSUpstreamError(503))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_error"
    assert result["message"] == _UPSTREAM_MESSAGE


@pytest.mark.unit
async def test_timeout_error_maps_to_upstream_timeout_json(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=DPSTimeoutError("slow"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_timeout"
    assert result["message"] == _UPSTREAM_MESSAGE


@pytest.mark.unit
async def test_network_error_maps_to_upstream_unreachable_json(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=DPSNetworkError("dns"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_unreachable"
    assert result["message"] == _UPSTREAM_MESSAGE


@pytest.mark.unit
async def test_invalid_response_maps_to_upstream_error_json(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(parties=DPSInvalidResponseError("bad json"))
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is False
    assert result["error"] == "upstream_error"
    assert result["message"] == _UPSTREAM_MESSAGE


@pytest.mark.unit
async def test_customer_call_auth_error_also_maps_cleanly(make_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tool(
        parties=[_full_party()],
        customers_by_party=DPSAuthError("nope"),
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["error"] == "auth_failed"
    assert result["message"] == _UPSTREAM_MESSAGE


@pytest.mark.unit
async def test_successful_identification_caches_full_candidates(make_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_party()
    customer = _customer()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    result = await _call(tool, rodne_cislo="8753189467")
    assert result["found"] is True
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    assert cached["rc_last4"] == "9467"
    assert cached["identification_method"] == "rodne_cislo"
    assert cached["identification_value"] == "8753189467"
    [cached_candidate] = cached["candidates"]
    assert cached_candidate["party_id"] == "PARTY_4482259100"
    assert cached_candidate["customer_id"] == "4482259100"
    # Contacts still cached for downstream tools
    assert any(c["type"] == "mobile" for c in cached_candidate["contacts"])


# ---- identifikacia_op ----


@pytest.fixture
def make_op_tool():
    """Factory specifically for the identifikacia_op tool."""

    def _factory(
        parties=None,
        customers_by_party=None,
        max_candidates: int = 10,
    ):
        stub = _StubClient(parties=parties, customers_by_party=customers_by_party)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_op"], stub

    return _factory


@pytest.mark.unit
async def test_op_rejects_empty_input(make_op_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_op_tool()
    result = await _call(tool, cislo_op="")
    assert result["found"] is False
    assert result["error"] == "invalid_input"
    assert "občianskeho preukazu" in result["message"]
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        "A1234567",  # 1 letter + 7 digits
        "ABCDEF12",  # 6 letters + 2 digits
        "12345",  # too short
        "A2B345678",  # mixed
        " ",  # blank
        "12345678",  # all-numeric: no longer accepted (no legacy SK OP)
        "123456789",  # 9 digits
        "1234567",  # 7 digits
        "AB12345",  # 5 digits (one short)
        "AB1234567",  # 7 digits (one over)
    ],
)
async def test_op_rejects_bad_format(make_op_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_op_tool()
    result = await _call(tool, cislo_op=bad)
    assert result["error"] == "invalid_input"
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("good_input", "expected_normalized"),
    [
        ("AB123456", "AB123456"),
        ("ab123456", "AB123456"),  # lowercase normalized
        (" AB123456 ", "AB123456"),  # leading/trailing whitespace
        ("AB-123456", "AB123456"),  # hyphen separator
        ("AB 123 456", "AB123456"),  # spaces between groups
        ("ea-123456", "EA123456"),  # combo: lowercase + hyphen
    ],
)
async def test_op_valid_format_reaches_party_call_with_correct_type(
    make_op_tool,
    conv,  # noqa: ARG001
    good_input,
    expected_normalized,
) -> None:
    tool, stub = make_op_tool(parties=[])
    await _call(tool, cislo_op=good_input)
    assert len(stub.party_calls) == 1
    called_id, called_type = stub.party_calls[0]
    assert called_id == expected_normalized
    assert called_type == "nationalIdentityCard"


@pytest.mark.unit
async def test_op_single_match_returns_name_only(make_op_tool, conv) -> None:  # noqa: ARG001
    party = _full_party()  # default name "Tester AT NECHYTAT", id PARTY_4482259100
    customer = _customer()
    tool, _ = make_op_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    result = await _call(tool, cislo_op="MM852148")
    assert result == {"found": True, "name": "Tester AT NECHYTAT"}


@pytest.mark.unit
async def test_op_not_found_uses_op_specific_message(make_op_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_op_tool(parties=[])
    result = await _call(tool, cislo_op="MM852148")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Zákazníka s týmto číslom občianskeho preukazu sa nepodarilo nájsť.",
    }


@pytest.mark.unit
async def test_op_caches_full_candidates(make_op_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_party()
    customer = _customer()
    tool, _ = make_op_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    await _call(tool, cislo_op="MM852148")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cached_candidate] = cached["candidates"]
    assert cached_candidate["party_id"] == "PARTY_4482259100"
    assert cached_candidate["customer_id"] == "4482259100"


@pytest.mark.unit
async def test_op_upstream_error_uses_unified_message(make_op_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.dps_get_client import DPSAuthError

    tool, _ = make_op_tool(parties=DPSAuthError("bad token"))
    result = await _call(tool, cislo_op="MM852148")
    assert result["error"] == "auth_failed"
    assert result["message"] == "Vyskytol sa technický problém. Prepojím vás na operátora."


# ---- hidden_tools (visibility control) ----


@pytest.mark.unit
def test_hidden_tools_are_not_registered() -> None:
    """`hidden_tools` keeps op/pas out of the registered set; others stay visible."""
    stub = _StubClient()
    fake = _FakeMCP()
    registry = ToolRegistry(fake)  # type: ignore[arg-type]
    identity_tools.register(
        registry,
        client=stub,
        hidden_tools=frozenset({"identifikacia_op", "identifikacia_pas"}),
    )
    assert "identifikacia_op" not in fake.registered
    assert "identifikacia_pas" not in fake.registered
    # A non-hidden tool is still registered.
    assert "identifikacia_rodne_cislo" in fake.registered


# ---- identifikacia_pas ----


@pytest.fixture
def make_pas_tool():
    """Factory specifically for the identifikacia_pas tool."""

    def _factory(
        parties=None,
        customers_by_party=None,
        max_candidates: int = 10,
    ):
        stub = _StubClient(parties=parties, customers_by_party=customers_by_party)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_pas"], stub

    return _factory


@pytest.mark.unit
async def test_pas_rejects_empty_input(make_pas_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_pas_tool()
    result = await _call(tool, cislo_pasu="")
    assert result["found"] is False
    assert result["error"] == "invalid_input"
    assert "cestovného pasu" in result["message"]
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["123", "ABCD12345", "A1234567890", " "])
async def test_pas_rejects_bad_format(make_pas_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_pas_tool()
    result = await _call(tool, cislo_pasu=bad)
    assert result["error"] == "invalid_input"
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("good", ["BR154151", "br154151", " BR154151 ", "AB123456", "X1234567"])
async def test_pas_valid_format_reaches_party_call_with_correct_type(
    make_pas_tool,
    conv,  # noqa: ARG001
    good,
) -> None:
    tool, stub = make_pas_tool(parties=[])
    await _call(tool, cislo_pasu=good)
    assert len(stub.party_calls) == 1
    called_id, called_type = stub.party_calls[0]
    assert called_id == good.strip().upper()
    assert called_type == "passport"


@pytest.mark.unit
async def test_pas_single_match_returns_name_only(make_pas_tool, conv) -> None:  # noqa: ARG001
    party = _full_party()
    customer = _customer()
    tool, _ = make_pas_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    result = await _call(tool, cislo_pasu="BR154151")
    assert result == {"found": True, "name": "Tester AT NECHYTAT"}


@pytest.mark.unit
async def test_pas_not_found_uses_pas_specific_message(make_pas_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_pas_tool(parties=[])
    result = await _call(tool, cislo_pasu="BR154151")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Zákazníka s týmto číslom cestovného pasu sa nepodarilo nájsť.",
    }


@pytest.mark.unit
async def test_pas_upstream_error_uses_unified_message(make_pas_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_pas_tool(parties=DPSAuthError("bad token"))
    result = await _call(tool, cislo_pasu="BR154151")
    assert result["error"] == "auth_failed"
    assert result["message"] == "Vyskytol sa technický problém. Prepojím vás na operátora."


@pytest.mark.unit
async def test_pas_caches_full_candidates(make_pas_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_party()
    customer = _customer()
    tool, _ = make_pas_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    await _call(tool, cislo_pasu="BR154151")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cached_candidate] = cached["candidates"]
    assert cached_candidate["party_id"] == "PARTY_4482259100"
    assert cached_candidate["customer_id"] == "4482259100"


# ---- identifikacia_ico ----


def _full_org_party(party_id: str = "PARTY_2648241400", name: str = "Rmc S.R.O.") -> dict:
    return {
        "id": party_id,
        "status": None,
        "entityType": "Party",
        "type": "organization",
        "organization": {
            "tradingName": name,
            "organizationIdentifications": [
                {
                    "type": "subjectRegistrationId",
                    "identificationId": "86316923",
                    "name": "registrationNumber",
                },
            ],
        },
        "contacts": [],
    }


@pytest.fixture
def make_ico_tool():
    """Factory specifically for the identifikacia_ico tool."""

    def _factory(
        parties=None,
        customers_by_party=None,
        max_candidates: int = 10,
    ):
        stub = _StubClient(parties=parties, customers_by_party=customers_by_party)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_ico"], stub

    return _factory


@pytest.mark.unit
async def test_ico_rejects_empty_input(make_ico_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_ico_tool()
    result = await _call(tool, ico="")
    assert result["found"] is False
    assert result["error"] == "invalid_input"
    assert "IČO" in result["message"]
    assert stub.party_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["1234567", "123456789", "ABCDEFGH", "1234567a"])
async def test_ico_rejects_bad_format(make_ico_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_ico_tool()
    result = await _call(tool, ico=bad)
    assert result["error"] == "invalid_input"
    assert stub.party_calls == []


@pytest.mark.unit
async def test_ico_valid_format_reaches_party_call_with_correct_type(
    make_ico_tool,
    conv,  # noqa: ARG001
) -> None:
    tool, stub = make_ico_tool(parties=[])
    await _call(tool, ico="86316923")
    assert len(stub.party_calls) == 1
    called_id, called_type = stub.party_calls[0]
    assert called_id == "86316923"
    assert called_type == "subjectRegistrationId"


@pytest.mark.unit
async def test_ico_single_match_returns_org_name(make_ico_tool, conv) -> None:  # noqa: ARG001
    party = _full_org_party()
    tool, _ = make_ico_tool(
        parties=[party],
        customers_by_party={"PARTY_2648241400": []},
    )
    result = await _call(tool, ico="86316923")
    assert result == {"found": True, "name": "Rmc S.R.O."}


@pytest.mark.unit
async def test_ico_not_found_uses_ico_specific_message(make_ico_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_ico_tool(parties=[])
    result = await _call(tool, ico="00000000")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Spoločnosť s týmto IČO sa nepodarilo nájsť.",
    }


@pytest.mark.unit
async def test_ico_caches_party_id_and_trading_name(make_ico_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_org_party()
    tool, _ = make_ico_tool(
        parties=[party],
        customers_by_party={"PARTY_2648241400": []},
    )
    await _call(tool, ico="86316923")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cached_candidate] = cached["candidates"]
    assert cached_candidate["party_id"] == "PARTY_2648241400"
    assert cached_candidate["name"] == "Rmc S.R.O."


@pytest.mark.unit
async def test_ico_org_candidate_has_null_given_and_family_name(
    make_ico_tool,
    conv,  # noqa: ARG001
) -> None:
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_org_party()
    tool, _ = make_ico_tool(
        parties=[party],
        customers_by_party={"PARTY_2648241400": []},
    )
    await _call(tool, ico="86316923")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cached_candidate] = cached["candidates"]
    assert cached_candidate["given_name"] is None
    assert cached_candidate["family_name"] is None


# ---- identifikacia_kod_zakaznika ----


@pytest.fixture
def make_kod_tool():
    def _factory(**kwargs):
        from svc.mcp_telekom_identity import tools as identity_tools

        stub = _StubClient(
            parties=kwargs.pop("parties", None),
            customers_by_party=kwargs.pop("customers_by_party", None),
            customer_by_id_map=kwargs.pop("customer_by_id_map", None),
            billing_account_by_id_map=kwargs.pop("billing_account_by_id_map", None),
        )
        max_candidates = kwargs.pop("max_candidates", 10)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_kod_zakaznika"], stub

    return _factory


def _b2c_customer(
    customer_id: str = "1002203200",
    name: str = "Muziková,Stano",
    billing_account_ids: list[str] | None = None,
) -> dict:
    """B2C customer with comma-reversed surname,givenName name format."""
    accounts = []
    if billing_account_ids:
        accounts = [
            {
                "id": customer_id,
                "type": "individual",
                "billingAccounts": [{"id": bid} for bid in billing_account_ids],
            }
        ]
    return {
        "id": customer_id,
        "name": name,
        "status": "active",
        "marketSegment": "Basic",
        "customerSegment": "B2C",
        "engagedParty": {"entityReferredType": "Party", "id": f"PARTY_{customer_id}"},
        "customerAccounts": accounts,
        "characteristics": [],
    }


def _b2b_customer(
    customer_id: str = "2300000400", name: str = "Creditinfo Slovakia, S.R.O."
) -> dict:
    return {
        "id": customer_id,
        "name": name,
        "status": "active",
        "marketSegment": "Basic",
        "customerSegment": "B2B",
        "engagedParty": {"entityReferredType": "Party", "id": f"PARTY_{customer_id}"},
        "customerAccounts": [],
        "characteristics": [],
    }


@pytest.mark.unit
async def test_kod_rejects_empty(make_kod_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_kod_tool()
    result = await _call(tool, kod_zakaznika="")
    assert result["error"] == "invalid_input"
    assert stub.customer_by_id_calls == []
    assert stub.billing_account_by_id_calls == []


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["abc", "1234567", "1234567890123", "12-345-678", " "])
async def test_kod_rejects_bad_format(make_kod_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_kod_tool()
    result = await _call(tool, kod_zakaznika=bad)
    assert result["error"] == "invalid_input"
    assert stub.customer_by_id_calls == []


@pytest.mark.unit
async def test_kod_b2c_customer_id_reverses_name(make_kod_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, stub = make_kod_tool(customer_by_id_map={"1002203200": cust})
    result = await _call(tool, kod_zakaznika="1002203200")
    assert result == {"found": True, "name": "Stano Muziková"}
    assert stub.customer_by_id_calls == ["1002203200"]
    assert stub.billing_account_by_id_calls == []


@pytest.mark.unit
async def test_kod_b2b_customer_id_keeps_name_with_comma(make_kod_tool, conv) -> None:  # noqa: ARG001
    cust = _b2b_customer("2300000400", "Creditinfo Slovakia, S.R.O.")
    tool, _ = make_kod_tool(customer_by_id_map={"2300000400": cust})
    result = await _call(tool, kod_zakaznika="2300000400")
    assert result == {"found": True, "name": "Creditinfo Slovakia, S.R.O."}


@pytest.mark.unit
async def test_kod_billing_account_resolves_to_customer(make_kod_tool, conv) -> None:  # noqa: ARG001
    ba = {"id": "1002203204", "customer": {"id": "1002203200"}}
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, stub = make_kod_tool(
        billing_account_by_id_map={"1002203204": ba},
        customer_by_id_map={"1002203200": cust},
    )
    result = await _call(tool, kod_zakaznika="1002203204")
    assert result == {"found": True, "name": "Stano Muziková"}
    assert stub.billing_account_by_id_calls == ["1002203204"]
    assert stub.customer_by_id_calls == ["1002203200"]


@pytest.mark.unit
async def test_kod_billing_account_not_found_returns_not_found(make_kod_tool, conv) -> None:  # noqa: ARG001
    tool, stub = make_kod_tool(
        billing_account_by_id_map={"9999999999": None},
    )
    result = await _call(tool, kod_zakaznika="9999999999")
    assert result["error"] == "not_found"
    assert stub.customer_by_id_calls == []  # no fanout to customer


@pytest.mark.unit
async def test_kod_customer_id_not_found_returns_not_found(make_kod_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_kod_tool(customer_by_id_map={"4432948400": None})
    result = await _call(tool, kod_zakaznika="4432948400")
    assert result == {
        "found": False,
        "error": "not_found",
        "message": "Zákazníka s týmto kódom sa nepodarilo nájsť.",
    }


@pytest.mark.unit
async def test_kod_caches_candidate(make_kod_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_kod_tool(customer_by_id_map={"1002203200": cust})
    await _call(tool, kod_zakaznika="1002203200")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    assert cached["identification_method"] == "kod_zakaznika"
    assert cached["identification_value"] == "1002203200"
    assert cached["rc_last4"] == "3200"
    [cand] = cached["candidates"]
    assert cand["customer_id"] == "1002203200"
    assert cand["party_id"] == "PARTY_1002203200"
    assert cand["name"] == "Stano Muziková"
    assert cand["given_name"] == "Stano"
    assert cand["family_name"] == "Muziková"
    assert cand["customer_segment"] == "B2C"
    # Party-derived fields are empty for the customer-only flow
    assert cand["contacts"] == []
    assert cand["identifications"] == []
    assert cand["billing_account_ids"] == []
    assert cand["auth_rc_last4"] is None


@pytest.mark.unit
async def test_kod_upstream_error_uses_unified_message(make_kod_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.dps_get_client import DPSAuthError

    tool, _ = make_kod_tool(customer_by_id_map=DPSAuthError("bad token"))
    result = await _call(tool, kod_zakaznika="1002203200")
    assert result["error"] == "auth_failed"
    assert result["message"] == "Vyskytol sa technický problém. Prepojím vás na operátora."


# ---- identifikacia_telefon ----


@pytest.fixture
def make_tel_tool():
    def _factory(**kwargs):
        stub = _StubClient(
            products_by_public_identifier_map=kwargs.pop("products_by_public_identifier_map", None),
            customer_by_id_map=kwargs.pop("customer_by_id_map", None),
        )
        max_candidates = kwargs.pop("max_candidates", 10)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_telefon"], stub

    return _factory


@pytest.mark.unit
async def test_tel_rejects_empty(make_tel_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tel_tool()
    result = await _call(tool, telefon="")
    assert result["error"] == "invalid_input"


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    ["abc", "123", "+421abc", "421-abc-def", " "],
)
async def test_tel_rejects_bad_format(make_tel_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_tel_tool()
    result = await _call(tool, telefon=bad)
    assert result["error"] == "invalid_input"
    assert stub.products_by_public_identifier_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected_intl"),
    [
        ("0902804660", "421902804660"),
        ("+421902804660", "421902804660"),
        ("421902804660", "421902804660"),
        ("00421902804660", "421902804660"),
        (" 0902 804 660 ", "421902804660"),
        ("0902-804-660", "421902804660"),
        ("(0902) 804 660", "421902804660"),
    ],
)
async def test_tel_normalizes_and_queries_intl_format(
    make_tel_tool,
    conv,  # noqa: ARG001
    raw,
    expected_intl,
) -> None:
    tool, stub = make_tel_tool(products_by_public_identifier_map={expected_intl: []})
    await _call(tool, telefon=raw)
    assert stub.products_by_public_identifier_calls == [expected_intl]


@pytest.mark.unit
async def test_tel_single_match(make_tel_tool, conv) -> None:  # noqa: ARG001
    product = {
        "id": "1-A6FN4UEC",
        "publicIdentifier": "421902804660",
        "customer": {"id": "1002203200"},
        "status": "Active",
    }
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_tel_tool(
        products_by_public_identifier_map={"421902804660": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    result = await _call(tool, telefon="0902804660")
    assert result == {"found": True, "name": "Stano Muziková"}


@pytest.mark.unit
async def test_tel_no_products_returns_not_found(make_tel_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_tel_tool(products_by_public_identifier_map={"421000000000": []})
    result = await _call(tool, telefon="0000000000")
    assert result["error"] == "not_found"


@pytest.mark.unit
async def test_tel_product_without_customer_returns_not_found(make_tel_tool, conv) -> None:  # noqa: ARG001
    product_no_cust = {"id": "P1", "publicIdentifier": "421902804660", "status": "Active"}
    tool, _ = make_tel_tool(
        products_by_public_identifier_map={"421902804660": [product_no_cust]},
    )
    result = await _call(tool, telefon="0902804660")
    assert result["error"] == "not_found"


@pytest.mark.unit
async def test_tel_multiple_customers_returns_multi_match(make_tel_tool, conv) -> None:  # noqa: ARG001
    products = [
        {"id": "P1", "publicIdentifier": "421902804660", "customer": {"id": "1002203200"}},
        {"id": "P2", "publicIdentifier": "421902804660", "customer": {"id": "4103349400"}},
    ]
    cust1 = _b2c_customer("1002203200", "Muziková,Stano")
    cust2 = _b2c_customer("4103349400", "Dorcak,Valent")
    tool, _ = make_tel_tool(
        products_by_public_identifier_map={"421902804660": products},
        customer_by_id_map={"1002203200": cust1, "4103349400": cust2},
    )
    result = await _call(tool, telefon="0902804660")
    assert result["found"] is True
    assert result["multiple_matches"] is True
    assert set(result["names"]) == {"Stano Muziková", "Valent Dorcak"}


@pytest.mark.unit
async def test_tel_caches_candidate(make_tel_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    product = {"id": "P1", "publicIdentifier": "421902804660", "customer": {"id": "1002203200"}}
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_tel_tool(
        products_by_public_identifier_map={"421902804660": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    await _call(tool, telefon="0902804660")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cand] = cached["candidates"]
    assert cand["customer_id"] == "1002203200"
    assert cand["name"] == "Stano Muziková"


@pytest.mark.unit
async def test_tel_upstream_error_uses_unified_message(make_tel_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.dps_get_client import DPSAuthError

    tool, _ = make_tel_tool(products_by_public_identifier_map=DPSAuthError("bad token"))
    result = await _call(tool, telefon="0902804660")
    assert result["error"] == "auth_failed"
    assert result["message"] == "Vyskytol sa technický problém. Prepojím vás na operátora."


# ---- identifikacia_seriove_cislo ----


@pytest.fixture
def make_serial_tool():
    def _factory(**kwargs):
        stub = _StubClient(
            products_by_serial_number_map=kwargs.pop("products_by_serial_number_map", None),
            customer_by_id_map=kwargs.pop("customer_by_id_map", None),
        )
        max_candidates = kwargs.pop("max_candidates", 10)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return fake.registered["identifikacia_seriove_cislo"], stub

    return _factory


@pytest.mark.unit
async def test_serial_rejects_empty(make_serial_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_serial_tool()
    result = await _call(tool, seriove_cislo="")
    assert result["error"] == "invalid_input"


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["abc", "1234567", "#" * 12, "AB CD!1234", " ", "A" * 31])
async def test_serial_rejects_bad_format(make_serial_tool, conv, bad) -> None:  # noqa: ARG001
    tool, stub = make_serial_tool()
    result = await _call(tool, seriove_cislo=bad)
    assert result["error"] == "invalid_input"
    assert stub.products_by_serial_number_calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("M91450EB0603", "M91450EB0603"),
        ("m91450eb0603", "M91450EB0603"),  # lowercase normalized
        (" M91450EB0603 ", "M91450EB0603"),  # whitespace
        ("M9145-0EB-0603", "M91450EB0603"),  # hyphens stripped
        ("M9145/0EB/0603", "M91450EB0603"),  # slashes stripped
        ("12345678", "12345678"),  # minimum length, all digits
        ("A" * 30, "A" * 30),  # maximum length
    ],
)
async def test_serial_normalizes_and_queries(
    make_serial_tool,
    conv,  # noqa: ARG001
    raw,
    expected,
) -> None:
    tool, stub = make_serial_tool(products_by_serial_number_map={expected: []})
    await _call(tool, seriove_cislo=raw)
    assert stub.products_by_serial_number_calls == [expected]


@pytest.mark.unit
async def test_serial_single_match(make_serial_tool, conv) -> None:  # noqa: ARG001
    product = {
        "id": "M-2B1PT-1",
        "productSerialNumber": "M91450EB0603",
        "customer": {"id": "1002203200"},
        "status": "Active",
    }
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_serial_tool(
        products_by_serial_number_map={"M91450EB0603": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    result = await _call(tool, seriove_cislo="M91450EB0603")
    assert result == {"found": True, "name": "Stano Muziková"}


@pytest.mark.unit
async def test_serial_no_products_returns_not_found(make_serial_tool, conv) -> None:  # noqa: ARG001
    tool, _ = make_serial_tool(products_by_serial_number_map={"UNKNOWNSN001": []})
    result = await _call(tool, seriove_cislo="UNKNOWNSN001")
    assert result["error"] == "not_found"


@pytest.mark.unit
async def test_serial_product_without_customer_returns_not_found(make_serial_tool, conv) -> None:  # noqa: ARG001
    product_no_cust = {"id": "P1", "productSerialNumber": "M91450EB0603", "status": "Active"}
    tool, _ = make_serial_tool(products_by_serial_number_map={"M91450EB0603": [product_no_cust]})
    result = await _call(tool, seriove_cislo="M91450EB0603")
    assert result["error"] == "not_found"


@pytest.mark.unit
async def test_serial_caches_candidate(make_serial_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    product = {
        "id": "M-2B1PT-1",
        "productSerialNumber": "M91450EB0603",
        "customer": {"id": "1002203200"},
    }
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_serial_tool(
        products_by_serial_number_map={"M91450EB0603": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    await _call(tool, seriove_cislo="M91450EB0603")
    cached = _IDENTITY_STATE.get("conv-test")
    assert cached is not None
    [cand] = cached["candidates"]
    assert cand["customer_id"] == "1002203200"
    assert cand["name"] == "Stano Muziková"


@pytest.mark.unit
async def test_serial_upstream_error_uses_unified_message(make_serial_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.dps_get_client import DPSAuthError

    tool, _ = make_serial_tool(products_by_serial_number_map=DPSAuthError("bad token"))
    result = await _call(tool, seriove_cislo="M91450EB0603")
    assert result["error"] == "auth_failed"
    assert result["message"] == "Vyskytol sa technický problém. Prepojím vás na operátora."


# ---- NLP push on success ----


@pytest.mark.unit
async def test_kod_pushes_full_value_to_nlp(
    make_kod_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_kod_tool(customer_by_id_map={"1002203200": cust})
    await _call(tool, kod_zakaznika="1002203200")
    nlp_calls = _reset_identity_state_and_silence_nlp
    assert len(nlp_calls) == 1
    conv_id, named_entities = nlp_calls[0]
    assert conv_id == "conv-test"
    assert named_entities == {
        "identification_method": "kod_zakaznika",
        "identification": "1002203200",
    }


@pytest.mark.unit
async def test_telefon_pushes_normalized_intl_to_nlp(
    make_tel_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    product = {"id": "P1", "publicIdentifier": "421902804660", "customer": {"id": "1002203200"}}
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_tel_tool(
        products_by_public_identifier_map={"421902804660": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    await _call(tool, telefon="0902804660")  # local format
    nlp_calls = _reset_identity_state_and_silence_nlp
    assert len(nlp_calls) == 1
    _, named_entities = nlp_calls[0]
    assert named_entities["identification_method"] == "telefon"
    assert named_entities["identification"] == "421902804660"  # normalized intl form


@pytest.mark.unit
async def test_serial_pushes_normalized_value_to_nlp(
    make_serial_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    product = {"id": "P1", "productSerialNumber": "M91450EB0603", "customer": {"id": "1002203200"}}
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_serial_tool(
        products_by_serial_number_map={"M91450EB0603": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    await _call(tool, seriove_cislo="m9145-0eb-0603")  # lowercase + hyphens
    nlp_calls = _reset_identity_state_and_silence_nlp
    assert len(nlp_calls) == 1
    _, named_entities = nlp_calls[0]
    assert named_entities["identification_method"] == "seriove_cislo"
    assert named_entities["identification"] == "M91450EB0603"  # normalized


@pytest.mark.unit
async def test_rodne_cislo_pushes_only_last4_to_nlp(
    make_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    party = _full_party()
    customer = _customer()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    await _call(tool, rodne_cislo="8753189467")
    nlp_calls = _reset_identity_state_and_silence_nlp
    assert len(nlp_calls) == 1
    _, named_entities = nlp_calls[0]
    assert named_entities == {
        "identification_method": "rodne_cislo",
        "identification": "last4=9467",
    }


@pytest.mark.unit
async def test_op_pushes_only_last4_to_nlp(
    make_op_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    party = _full_party()
    customer = _customer()
    tool, _ = make_op_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    await _call(tool, cislo_op="MM852148")
    nlp_calls = _reset_identity_state_and_silence_nlp
    _, named_entities = nlp_calls[0]
    assert named_entities == {
        "identification_method": "op",
        "identification": "last4=2148",
    }


@pytest.mark.unit
async def test_ico_pushes_full_value_to_nlp(
    make_ico_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    party = _full_org_party("PARTY_2648241400", "Rmc S.R.O.")
    tool, _ = make_ico_tool(parties=[party], customers_by_party={"PARTY_2648241400": []})
    await _call(tool, ico="86316923")
    nlp_calls = _reset_identity_state_and_silence_nlp
    _, named_entities = nlp_calls[0]
    assert named_entities == {
        "identification_method": "ico",
        "identification": "86316923",
    }


@pytest.mark.unit
async def test_not_found_does_not_push_to_nlp(
    make_kod_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    tool, _ = make_kod_tool(customer_by_id_map={"4432948400": None})
    await _call(tool, kod_zakaznika="4432948400")
    assert _reset_identity_state_and_silence_nlp == []  # no push on not_found


@pytest.mark.unit
async def test_invalid_input_does_not_push_to_nlp(
    make_kod_tool,
    conv,  # noqa: ARG001
    _reset_identity_state_and_silence_nlp,
) -> None:
    tool, _ = make_kod_tool()
    await _call(tool, kod_zakaznika="abc")
    assert _reset_identity_state_and_silence_nlp == []


@pytest.mark.unit
async def test_no_conversation_id_does_not_push_to_nlp(
    make_kod_tool, _reset_identity_state_and_silence_nlp
) -> None:
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_kod_tool(customer_by_id_map={"1002203200": cust})
    # NOTE: no `conv` fixture used — ContextVars default to empty
    await _call(tool, kod_zakaznika="1002203200")
    assert _reset_identity_state_and_silence_nlp == []


# ---- billing_account_ids extraction ----


@pytest.mark.unit
async def test_kod_zakaznika_extracts_billing_account_ids(make_kod_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204", "1002203202"],
    )
    tool, _ = make_kod_tool(customer_by_id_map={"1002203200": cust})
    await _call(tool, kod_zakaznika="1002203200")
    cached = _IDENTITY_STATE.get("conv-test")
    [cand] = cached["candidates"]
    assert cand["billing_account_ids"] == ["1002203204", "1002203202"]
    assert cand["auth_rc_last4"] is None


# ---- auth_rc_last4 extraction ----


@pytest.mark.unit
async def test_rodne_cislo_caches_auth_rc_last4(make_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _IDENTITY_STATE

    party = _full_party()  # already has socialSecurityNumber=8753189467
    customer = _customer()
    tool, _ = make_tool(
        parties=[party],
        customers_by_party={"PARTY_4482259100": [customer]},
    )
    await _call(tool, rodne_cislo="8753189467")
    cached = _IDENTITY_STATE.get("conv-test")
    [cand] = cached["candidates"]
    assert cand["auth_rc_last4"] == "9467"


# ---- NLP mirror tests ----


@pytest.mark.unit
async def test_nlp_mirror_captures_set_state_writes(make_kod_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _NLP_MIRROR_STATE

    cust = _b2c_customer("1002203200", "Muziková,Stano")
    tool, _ = make_kod_tool(customer_by_id_map={"1002203200": cust})
    await _call(tool, kod_zakaznika="1002203200")
    mirror = _NLP_MIRROR_STATE.get("conv-test")
    assert mirror is not None
    assert mirror["identification_method"] == "kod_zakaznika"
    assert mirror["identification"] == "1002203200"


@pytest.mark.unit
def test_nlp_get_named_entities_returns_empty_for_unknown_conv() -> None:
    from svc.mcp_telekom_identity.tools import _nlp_get_named_entities

    assert _nlp_get_named_entities("nonexistent-conv") == {}


# ---- autentifikacia ----


@pytest.fixture
def make_auth_tool():
    def _factory(**kwargs):
        stub = _StubClient(
            parties=kwargs.pop("parties", None),
            customers_by_party=kwargs.pop("customers_by_party", None),
            customer_by_id_map=kwargs.pop("customer_by_id_map", None),
            billing_account_by_id_map=kwargs.pop("billing_account_by_id_map", None),
            products_by_public_identifier_map=kwargs.pop("products_by_public_identifier_map", None),
            products_by_serial_number_map=kwargs.pop("products_by_serial_number_map", None),
            party_by_id_map=kwargs.pop("party_by_id_map", None),
        )
        max_candidates = kwargs.pop("max_candidates", 10)
        fake = _FakeMCP()
        registry = ToolRegistry(fake)  # type: ignore[arg-type]
        identity_tools.register(registry, client=stub, max_candidates=max_candidates)
        return {
            "autentifikacia": fake.registered["autentifikacia"],
            "nastav_test_kontext": fake.registered["nastav_test_kontext"],
            "identifikacia_rodne_cislo": fake.registered["identifikacia_rodne_cislo"],
            "identifikacia_kod_zakaznika": fake.registered["identifikacia_kod_zakaznika"],
            "identifikacia_telefon": fake.registered["identifikacia_telefon"],
        }, stub

    return _factory


@pytest.mark.unit
async def test_auth_requires_identification_first(make_auth_tool, conv) -> None:  # noqa: ARG001
    tools, _ = make_auth_tool()
    result = await _call(tools["autentifikacia"])
    assert result["error"] == "identification_required"
    assert not result["authenticated"]


@pytest.mark.unit
async def test_auth_after_rodne_cislo_credits_factor_4(make_auth_tool, conv) -> None:  # noqa: ARG001
    tools, _ = make_auth_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    # Identify via RČ first
    await _call(tools["identifikacia_rodne_cislo"], rodne_cislo="8753189467")
    # First auth call with no args: factor 4 auto-credited (and factor 1 either way),
    # standard needs 2 → next factor is name.
    result = await _call(tools["autentifikacia"])
    assert not result["authenticated"]
    assert result["next_factor"] == "name"
    # Provide name (lenient match — strip diacritics, case-insensitive, both orders)
    result = await _call(tools["autentifikacia"], meno_priezvisko="at nechytat tester")
    assert result["authenticated"] is True
    assert result["level"] == "standard"


@pytest.mark.unit
async def test_auth_sensitive_needs_three_factors(make_auth_tool, conv) -> None:  # noqa: ARG001
    tools, _ = make_auth_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    await _call(tools["identifikacia_rodne_cislo"], rodne_cislo="8753189467")
    await _call(tools["nastav_test_kontext"], authentication_type="sensitive")
    # factor 4 credited (RČ identification) + factor 2 (name) = 2 → need 3 for sensitive
    result = await _call(tools["autentifikacia"], meno_priezvisko="Tester AT NECHYTAT")
    assert not result["authenticated"]
    assert result["next_factor"] == "kod_adresata"


@pytest.mark.unit
async def test_auth_kod_zakaznika_billing_credits_factor_3(make_auth_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    tools, _ = make_auth_tool(
        billing_account_by_id_map={
            "1002203204": {"id": "1002203204", "customer": {"id": "1002203200"}}
        },
        customer_by_id_map={"1002203200": cust},
    )
    # Identify via billing account (ends in 4 → kod_adresata credit)
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203204")
    # Standard needs 2 → factor 3 already credited, so next is name
    result = await _call(tools["autentifikacia"], meno_priezvisko="Stano Muziková")
    assert result["authenticated"] is True
    assert "kod_adresata" in result["factors_satisfied"]
    assert "name" in result["factors_satisfied"]


@pytest.mark.unit
async def test_auth_kod_zakaznika_customer_id_does_not_credit_factor_3(
    make_auth_tool,
    conv,  # noqa: ARG001
) -> None:
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    tools, _ = make_auth_tool(customer_by_id_map={"1002203200": cust})
    # Identify by customer id (ends in 0 → NO factor 3 credit)
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203200")
    # Standard needs 2 — factor 1 (input_source missing → skipped), no other credits
    # So we need name + one of (kod_adresata, rc_last4)
    result = await _call(tools["autentifikacia"], meno_priezvisko="Stano Muziková")
    assert not result["authenticated"]
    # Next factor should be kod_adresata
    assert result["next_factor"] == "kod_adresata"


@pytest.mark.unit
async def test_auth_trusted_source_via_party_contacts(make_auth_tool, conv) -> None:  # noqa: ARG001
    # _full_party() has Party.contacts including {type: mobile, medium.number: "0902555002"}
    tools, _ = make_auth_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    await _call(tools["identifikacia_rodne_cislo"], rodne_cislo="8753189467")
    # Caller from the same mobile — factor 1 should auto-credit
    await _call(tools["nastav_test_kontext"], input_source="0902555002")
    result = await _call(tools["autentifikacia"])
    # factor 1 credited + factor 4 (RČ identification) = 2 → standard authenticated immediately
    assert result["authenticated"] is True
    assert result["level"] == "standard"
    assert "trusted_source" in result["factors_satisfied"]


@pytest.mark.unit
async def test_auth_trusted_source_via_telefon_identification_msisdn(
    make_auth_tool,
    conv,  # noqa: ARG001
) -> None:
    # When the customer was identified via `identifikacia_telefon`, the queried MSISDN
    # is injected into candidate.contacts (no Party fetch needed). Setting input_source
    # to the same number must auto-credit factor 1, even though the Customer-only flow
    # didn't fetch the Party.
    cust = _b2c_customer("1002203200", "Muziková,Stano")
    product = {
        "id": "P1",
        "publicIdentifier": "421902804660",
        "customer": {"id": "1002203200"},
    }
    tools, _ = make_auth_tool(
        products_by_public_identifier_map={"421902804660": [product]},
        customer_by_id_map={"1002203200": cust},
    )
    await _call(tools["identifikacia_telefon"], telefon="0902804660")
    # input_source as SK local form — `_check_trusted_source` normalizes both sides
    await _call(tools["nastav_test_kontext"], input_source="0902804660")
    # factor 1 credited (from injected contact) → only 1 more factor needed for standard
    result = await _call(tools["autentifikacia"])
    assert not result["authenticated"]
    assert "trusted_source" in result["factors_satisfied"]
    assert result["factors_remaining"] == 1
    assert result["next_factor"] == "name"


@pytest.mark.unit
async def test_auth_name_lenient_match(make_auth_tool, conv) -> None:  # noqa: ARG001
    tools, _ = make_auth_tool(
        parties=[_full_party()],
        customers_by_party={"PARTY_4482259100": [_customer()]},
    )
    await _call(tools["identifikacia_rodne_cislo"], rodne_cislo="8753189467")
    # Reverse order + lowercase + no diacritics (Party name is "Tester AT NECHYTAT")
    result = await _call(tools["autentifikacia"], meno_priezvisko="at nechytat tester")
    assert result["authenticated"] is True


@pytest.mark.unit
async def test_auth_out_of_order_rejected(make_auth_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    tools, _ = make_auth_tool(customer_by_id_map={"1002203200": cust})
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203200")
    # Tool expects name next; sending kod_adresata directly is out_of_order
    result = await _call(tools["autentifikacia"], kod_adresata="1002203204")
    assert result["error"] == "out_of_order"
    assert result["expected_factor"] == "name"


@pytest.mark.unit
async def test_auth_max_attempts_marks_factor_failed(make_auth_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    tools, _ = make_auth_tool(customer_by_id_map={"1002203200": cust})
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203200")
    # 3 bad attempts on name
    r1 = await _call(tools["autentifikacia"], meno_priezvisko="zly meno 1")
    assert r1["factor_failed"] == "name"
    assert r1["attempts_remaining"] == 2
    r2 = await _call(tools["autentifikacia"], meno_priezvisko="zly meno 2")
    assert r2["attempts_remaining"] == 1
    r3 = await _call(tools["autentifikacia"], meno_priezvisko="zly meno 3")
    # After 3rd failure, name moves to factors_failed and tool advances to next factor
    assert r3["next_factor"] == "kod_adresata"


@pytest.mark.unit
async def test_auth_skip_current_factor_advances(make_auth_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    tools, _ = make_auth_tool(customer_by_id_map={"1002203200": cust})
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203200")
    # Skip name → next should be kod_adresata
    result = await _call(tools["autentifikacia"], skip_current_factor=True)
    assert result["next_factor"] == "kod_adresata"


@pytest.mark.unit
async def test_auth_cannot_authenticate_when_all_factors_blocked(make_auth_tool, conv) -> None:  # noqa: ARG001
    cust = _b2c_customer("1002203200", "Muziková,Stano")  # no billing accounts, no contacts
    tools, _ = make_auth_tool(customer_by_id_map={"1002203200": cust})
    await _call(tools["identifikacia_kod_zakaznika"], kod_zakaznika="1002203200")
    # Skip all factors
    await _call(tools["autentifikacia"], skip_current_factor=True)  # skip name
    await _call(tools["autentifikacia"], skip_current_factor=True)  # skip kod_adresata
    result = await _call(tools["autentifikacia"], skip_current_factor=True)  # skip rc_last4
    assert result["error"] == "cannot_authenticate"


@pytest.mark.unit
async def test_auth_lazy_fetches_party_for_rc_when_customer_only_identification(
    make_auth_tool,
    conv,  # noqa: ARG001
) -> None:
    # identifikacia_telefon → customer-only path → candidate.auth_rc_last4 is None
    cust = _b2c_customer(
        "1002203200",
        "Muziková,Stano",
        billing_account_ids=["1002203204"],
    )
    party_record = {
        "id": "PARTY_1002203200",
        "type": "individual",
        "individual": {
            "givenName": "Stano",
            "familyName": "Muziková",
            "individualIdentifications": [
                {"type": "socialSecurityNumber", "identificationId": "7304292105"},
            ],
        },
        "contacts": [],
    }
    tools, stub = make_auth_tool(
        products_by_public_identifier_map={
            "421902804660": [
                {"id": "P1", "publicIdentifier": "421902804660", "customer": {"id": "1002203200"}}
            ]
        },
        customer_by_id_map={"1002203200": cust},
        party_by_id_map={"PARTY_1002203200": party_record},
    )
    await _call(tools["identifikacia_telefon"], telefon="0902804660")
    await _call(tools["nastav_test_kontext"], authentication_type="sensitive")
    # Name (factor 2) + kod_adresata (factor 3)
    await _call(tools["autentifikacia"], meno_priezvisko="Stano Muziková")
    await _call(tools["autentifikacia"], kod_adresata="1002203204")
    # Now factor 4 — tool must lazy-fetch the Party to read RČ
    result = await _call(tools["autentifikacia"], rc_last4="2105")
    assert result["authenticated"] is True
    assert result["level"] == "sensitive"
    assert "PARTY_1002203200" in stub.party_by_id_calls


@pytest.mark.unit
async def test_nastav_test_kontext_writes_to_mirror(make_auth_tool, conv) -> None:  # noqa: ARG001
    from svc.mcp_telekom_identity.tools import _NLP_MIRROR_STATE

    tools, _ = make_auth_tool()
    result = await _call(
        tools["nastav_test_kontext"],
        input_source="0902555002",
        authentication_type="sensitive",
    )
    assert result["ok"] is True
    mirror = _NLP_MIRROR_STATE.get("conv-test")
    assert mirror["input_source"] == "0902555002"
    assert mirror["authentication_type"] == "sensitive"
