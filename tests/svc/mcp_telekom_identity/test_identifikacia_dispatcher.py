"""Tests for the merged `identifikacia` dispatcher, classifier, validators and widgets."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from lib.mcp_service.legacy_compat import ToolRegistry
from svc.mcp_telekom_identity import tools as identity_tools
from svc.mcp_telekom_identity import widgets

_CONV = "conv-test"
_REAL_NLP_FLUSH = identity_tools._nlp_flush


# --------------------------------------------------------------------------- #
# Classifier + structural validators (pure functions)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # rodné číslo — README fixtures (all 10-digit, mod-11 valid)
        ("7304292105", "rodne_cislo"),
        ("8407160630", "rodne_cislo"),
        ("7210055589", "rodne_cislo"),
        ("6862147292", "rodne_cislo"),
        ("8753189467", "rodne_cislo"),
        # IČO — valid checksum
        ("86316923", "ico"),
        # kód zákazníka / fakturačný účet (no RČ/IČO structure)
        ("1002203200", "kod_zakaznika"),
        ("4482259100", "kod_zakaznika"),
        ("1002203204", "kod_zakaznika"),
        ("2300000400", "kod_zakaznika"),
        ("4108064300", "kod_zakaznika"),
        ("2315059402", "kod_zakaznika"),
        # telefón — local / international / spaced
        ("0902804660", "telefon"),
        ("+421902804660", "telefon"),
        ("421902804660", "telefon"),
        ("0902 804 660", "telefon"),
        # sériové číslo — alphanumeric, separators stripped
        ("M91450EB0603", "seriove_cislo"),
        ("m91450eb0603", "seriove_cislo"),
        ("M9145-0EB-0603", "seriove_cislo"),
        ("K5D0M374LXO", "seriove_cislo"),
    ],
)
def test_classifier_detects_type(value: str, expected: str) -> None:
    detected, alternatives = identity_tools._classify_identifier(value)
    assert detected == expected, (value, detected, alternatives)
    assert alternatives == []


@pytest.mark.unit
def test_classifier_unrecognized_returns_none() -> None:
    assert identity_tools._classify_identifier("") == (None, [])
    assert identity_tools._classify_identifier("ab!@") == (None, [])
    # letters present but not a valid serial length
    assert identity_tools._classify_identifier("AB12") == (None, [])


@pytest.mark.unit
def test_classifier_phone_rc_collision_is_ambiguous() -> None:
    # 0901010000 normalises to a SK MSISDN AND is a structurally valid 10-digit RČ.
    detected, alternatives = identity_tools._classify_identifier("0901010000")
    assert detected is None
    assert set(alternatives) == {"telefon", "rodne_cislo"}


@pytest.mark.unit
def test_classifier_known_kod_rc_collision_is_documented() -> None:
    # 4108064301 is a billing account that happens to parse as a valid RČ
    # (41/08/06 + mod-11). Auto-detect resolves it to rodne_cislo; the customer
    # picks the type in the dropdown for the rare miss. Documented, not a bug.
    detected, _ = identity_tools._classify_identifier("4108064301")
    assert detected == "rodne_cislo"


@pytest.mark.unit
@pytest.mark.parametrize(("ico", "ok"), [("86316923", True), ("12345678", False), ("00000000", False)])
def test_is_valid_ico(ico: str, ok: bool) -> None:
    assert identity_tools._is_valid_ico(ico) is ok


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rc", "ok"),
    [
        ("7304292105", True),   # mod-11 + valid date
        ("8753189467", True),
        ("6862147292", True),   # woman (month +50)
        ("1002203200", False),  # mod-11 fails
        ("7300002105", False),  # month 00
        ("12345", False),       # wrong length
    ],
)
def test_is_valid_rodne_cislo(rc: str, ok: bool) -> None:
    assert identity_tools._is_valid_rodne_cislo(rc) is ok


# --------------------------------------------------------------------------- #
# Widget builders
# --------------------------------------------------------------------------- #


def _find(node: Any, type_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            if n.get("type") == type_name:
                out.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return out


@pytest.mark.unit
def test_identifikacia_widget_shape() -> None:
    w = widgets.identifikacia_widget()
    assert w["type"] == "Form"
    inputs = _find(w, "Input")
    assert [i["name"] for i in inputs] == [widgets.IDENT_INPUT_KEY]
    selects = _find(w, "Select")
    assert len(selects) == 1
    sel = selects[0]
    assert sel["name"] == widgets.IDENT_TYPE_KEY
    assert sel["defaultValue"] == "auto"
    assert sel["options"][0]["value"] == "auto"
    # submit button carries the hidden submit utterance
    buttons = _find(w, "Button")
    submit = next(b for b in buttons if b.get("submit"))
    action = submit["onClickAction"]
    assert action["type"] == "as_buttons"
    assert action["payload"] == {"utterance": widgets.IDENT_SUBMIT_UTTERANCE, "hidden": True}


@pytest.mark.unit
@pytest.mark.parametrize("factor", ["name", "kod_adresata", "rc_last4"])
def test_auth_factor_widget_shape(factor: str) -> None:
    w = widgets.auth_factor_widget(factor)
    inputs = _find(w, "Input")
    assert inputs[0]["name"] == widgets.AUTH_FIELD_KEYS[factor]
    utterances = {
        b["onClickAction"]["payload"]["utterance"] for b in _find(w, "Button")
    }
    assert widgets.AUTH_SUBMIT_UTTERANCE in utterances
    assert widgets.AUTH_SKIP_UTTERANCE in utterances


# --------------------------------------------------------------------------- #
# Dispatcher — channel gating, widget render, auto-detect, widget submit
# --------------------------------------------------------------------------- #


class _CustomerStub:
    """Minimal DPS stub: every lookup resolves to empty/None unless seeded."""

    def __init__(self, customer_by_id: dict[str, dict | None]) -> None:
        self._map = customer_by_id
        self.customer_calls: list[str] = []
        self.party_calls: list[tuple[str, str]] = []

    async def get_customer_by_id(self, cid: str) -> dict | None:
        self.customer_calls.append(cid)
        return self._map.get(cid)

    async def get_parties_by_identification(self, ident: str, itype: str) -> list[dict]:
        self.party_calls.append((ident, itype))
        return []

    async def get_billing_account_by_id(self, bid: str) -> dict | None:  # noqa: ARG002
        return None

    async def get_products_by_public_identifier(self, msisdn: str) -> list[dict]:  # noqa: ARG002
        return []

    async def get_products_by_serial_number(self, serial: str) -> list[dict]:  # noqa: ARG002
        return []

    async def get_party_by_id(self, pid: str) -> dict | None:  # noqa: ARG002
        return None


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    identity_tools._IDENTITY_STATE = type(identity_tools._IDENTITY_STATE)(
        ttl_seconds=identity_tools._IDENTITY_TTL_SECONDS,
    )
    identity_tools._NLP_MIRROR_STATE = type(identity_tools._NLP_MIRROR_STATE)(
        ttl_seconds=identity_tools._NLP_MIRROR_TTL_SECONDS,
    )
    identity_tools._NLP_FLUSHED_STATE = type(identity_tools._NLP_FLUSHED_STATE)(
        ttl_seconds=identity_tools._NLP_MIRROR_TTL_SECONDS,
    )
    # Make _nlp_flush a no-op by default (avoid background HTTP threads in tests).
    monkeypatch.setattr(identity_tools, "_nlp_flush", lambda conv: None)


@pytest.fixture
def conv():
    token_c = current_conversation_id.set(_CONV)
    token_i = current_interaction_id.set("inter-test")
    try:
        yield
    finally:
        current_conversation_id.reset(token_c)
        current_interaction_id.reset(token_i)


def _make(customer_by_id: dict[str, dict | None] | None = None):
    stub = _CustomerStub(customer_by_id or {})

    class _FakeMCP:
        def __init__(self) -> None:
            self.registered: dict[str, Any] = {}

        def tool(self, *, name: str, description: str | None = None):  # noqa: ARG002
            def deco(fn):
                self.registered[name] = fn
                return fn

            return deco

    fake = _FakeMCP()
    identity_tools.register(ToolRegistry(fake), client=stub)  # type: ignore[arg-type]
    return fake.registered["identifikacia"], stub


def _seed(**entities: str) -> None:
    identity_tools._NLP_MIRROR_STATE.set(_CONV, dict(entities))


async def _call(tool, **kw) -> dict:
    return json.loads(await tool(**kw))


@pytest.mark.unit
async def test_chat_no_value_renders_widget(conv) -> None:  # noqa: ARG001
    _seed(Channel="chat")
    tool, _ = _make()
    result = await _call(tool)
    assert result["type"] == "bubble_widget_result"
    assert result["template"] == "identifikacia"
    assert result["widget"]["type"] == "Form"


@pytest.mark.unit
async def test_non_chat_no_value_asks_for_input(conv) -> None:  # noqa: ARG001
    _seed(Channel="voice")
    tool, _ = _make()
    result = await _call(tool)
    assert result["found"] is False
    assert result["error"] == "input_required"


@pytest.mark.unit
async def test_chat_ambiguous_auto_reprompts_widget(conv) -> None:  # noqa: ARG001
    _seed(Channel="chat")
    tool, _ = _make()
    # phone/RC collision + auto type → widget asking to pick the type
    result = await _call(tool, hodnota="0901010000", typ="auto")
    assert result["type"] == "bubble_widget_result"
    assert result["template"] == "identifikacia"


@pytest.mark.unit
async def test_widget_submit_reads_value_from_named_entities(conv) -> None:  # noqa: ARG001
    # Host wrote the widget values into named_entities and emitted the utterance;
    # the LLM calls identifikacia() with no args → tool reads them and identifies.
    customer = {
        "id": "1002203200",
        "name": "Novak,Jan",
        "customerSegment": "B2C",
        "engagedParty": {"id": "P1"},
    }
    _seed(Channel="chat", identifikacia_vstup="1002203200", identifikacia_typ="auto")
    tool, stub = _make(customer_by_id={"1002203200": customer})
    result = await _call(tool)
    assert result == {"found": True, "name": "Jan Novak"}
    assert stub.customer_calls == ["1002203200"]


@pytest.mark.unit
async def test_explicit_type_overrides_autodetect(conv) -> None:  # noqa: ARG001
    # "1002203200" auto-detects as kod_zakaznika (→ get_customer_by_id), but an
    # explicit typ="rodne_cislo" must force the RČ route (→ party lookup) instead.
    _seed(Channel="chat")
    tool, stub = _make()
    result = await _call(tool, hodnota="1002203200", typ="rodne_cislo")
    assert result["found"] is False  # stub party lookup is empty → not_found
    assert stub.party_calls == [("1002203200", "socialSecurityNumber")]
    assert stub.customer_calls == []  # kod route NOT taken


# --------------------------------------------------------------------------- #
# Privacy: widget-submitted raw values are never pushed back to the NLP engine
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_do_not_flush_excludes_sensitive_widget_keys(monkeypatch) -> None:
    # Run the real _nlp_flush (the autouse fixture stubbed it to a no-op) with a
    # synchronous Thread and a capturing urlopen, and assert the PUT body excludes
    # the sensitive widget-input keys.
    monkeypatch.setattr(identity_tools, "_nlp_flush", _REAL_NLP_FLUSH)

    captured: list[dict] = []

    class _Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):  # noqa: ARG001
        captured.append(json.loads(req.data.decode()))
        return _Resp()

    class _SyncThread:
        def __init__(self, target=None, **kw):  # noqa: ARG002
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(identity_tools.urllib.request, "urlopen", _fake_urlopen)
    monkeypatch.setattr(identity_tools.threading, "Thread", _SyncThread)

    identity_tools._NLP_FLUSHED_STATE.set(_CONV, {})
    identity_tools._NLP_MIRROR_STATE.set(
        _CONV,
        {
            "identification": "1002203200",        # normal → pushed
            "identifikacia_vstup": "7304292105",   # sensitive → must NOT be pushed
            "autentifikacia_rc_last4": "2105",      # sensitive → must NOT be pushed
        },
    )

    identity_tools._nlp_flush(_CONV)

    assert len(captured) == 1
    pushed = captured[0]["named_entities"]
    assert pushed == {"identification": "1002203200"}
