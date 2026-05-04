"""Tests for the legacy ``mcp_server`` compatibility shim."""

from __future__ import annotations

import inspect

import pytest

from lib.boilerplate.logging import current_conversation_id, current_interaction_id
from lib.mcp_service.legacy_compat import _wrap_for_fastmcp, mcp_tool


@pytest.mark.unit
def test_wrap_strips_meta_from_signature() -> None:
    def legacy_tool(arg: str, _meta: dict | None = None) -> str:
        return arg

    wrapped = _wrap_for_fastmcp(legacy_tool)
    sig = inspect.signature(wrapped)
    assert "_meta" not in sig.parameters
    assert "arg" in sig.parameters


@pytest.mark.unit
def test_wrap_injects_meta_from_contextvars() -> None:
    captured: dict = {}

    def legacy_tool(arg: str, _meta: dict | None = None) -> str:
        captured["arg"] = arg
        captured["meta"] = _meta
        return arg

    wrapped = _wrap_for_fastmcp(legacy_tool)
    current_conversation_id.set("conv-99")
    current_interaction_id.set("int-12")
    try:
        result = wrapped(arg="hello")
    finally:
        current_conversation_id.set("")
        current_interaction_id.set("")

    assert result == "hello"
    assert captured["arg"] == "hello"
    assert captured["meta"]["conversation_id"] == "conv-99"
    assert captured["meta"]["interaction_id"] == "int-12"


@pytest.mark.unit
def test_wrap_overrides_explicit_meta() -> None:
    captured: dict = {}

    def legacy_tool(arg: str, _meta: dict | None = None) -> str:  # noqa: ARG001
        captured["meta"] = _meta
        return ""

    wrapped = _wrap_for_fastmcp(legacy_tool)
    current_conversation_id.set("real-conv")
    try:
        wrapped(arg="x", _meta={"conversation_id": "spoofed"})
    finally:
        current_conversation_id.set("")

    assert captured["meta"]["conversation_id"] == "real-conv"


@pytest.mark.unit
def test_mcp_tool_decorator_returns_function_unchanged() -> None:
    """Without a registry, the decorator is a no-op (function is returned as-is)."""

    @mcp_tool(name="x", description="y")
    def tool() -> str:
        return "ok"

    assert tool() == "ok"


@pytest.mark.unit
async def test_wrap_preserves_async_for_coroutine_functions() -> None:
    """Async tools must produce an awaitable wrapper, not a sync wrapper that returns a coroutine."""
    captured: dict = {}

    async def legacy_async_tool(arg: str, _meta: dict | None = None) -> str:
        captured["arg"] = arg
        captured["meta"] = _meta
        return arg.upper()

    wrapped = _wrap_for_fastmcp(legacy_async_tool)
    assert inspect.iscoroutinefunction(wrapped)

    current_conversation_id.set("conv-async")
    try:
        result = await wrapped(arg="hi")
    finally:
        current_conversation_id.set("")

    assert result == "HI"
    assert captured["arg"] == "hi"
    assert captured["meta"]["conversation_id"] == "conv-async"


@pytest.mark.unit
def test_wrap_keeps_sync_for_plain_functions() -> None:
    def legacy_sync_tool(arg: str) -> str:
        return arg.upper()

    wrapped = _wrap_for_fastmcp(legacy_sync_tool)
    assert not inspect.iscoroutinefunction(wrapped)
    assert wrapped(arg="x") == "X"
