"""Smoke tests for the tracing middleware that wraps the FastMCP HTTP transport."""

from __future__ import annotations

import pytest

from lib.boilerplate.logging import (
    current_conversation_id,
    current_interaction_id,
    current_trace_id,
)
from lib.mcp_service.middleware import TracingMiddleware


def _scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {"type": "http", "headers": headers, "method": "POST", "path": "/mcp"}


@pytest.mark.unit
async def test_middleware_binds_conversation_and_interaction_ids() -> None:
    captured: dict[str, str] = {}

    async def inner_app(scope, receive, send) -> None:  # noqa: ARG001, ANN001
        captured["conversation_id"] = current_conversation_id.get("")
        captured["interaction_id"] = current_interaction_id.get("")
        captured["trace_id"] = current_trace_id.get("")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    middleware = TracingMiddleware(inner_app)
    headers = [
        (b"x-conversation-id", b"conv-42"),
        (b"x-interaction-id", b"int-7"),
        (b"x-trace-id", b"DEADBEEF"),
    ]
    await middleware(_scope(headers), receive, send)

    assert captured["conversation_id"] == "conv-42"
    assert captured["interaction_id"] == "int-7"
    assert captured["trace_id"] == "DEADBEEF"
    # Response start frame must echo X-Trace-Id back
    start_frame = next(m for m in sent if m["type"] == "http.response.start")
    response_headers = dict(start_frame["headers"])
    assert response_headers[b"X-Trace-Id"] == b"DEADBEEF"


@pytest.mark.unit
async def test_middleware_generates_trace_id_when_absent() -> None:
    seen_trace: dict[str, str] = {}

    async def inner_app(scope, receive, send) -> None:  # noqa: ARG001, ANN001
        seen_trace["value"] = current_trace_id.get("")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message: dict) -> None:
        pass

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    middleware = TracingMiddleware(inner_app)
    await middleware(_scope([]), receive, send)

    assert len(seen_trace["value"]) == 8  # TRACE_ID_LENGTH


@pytest.mark.unit
async def test_middleware_truncates_oversized_trace_id() -> None:
    seen_trace: dict[str, str] = {}

    async def inner_app(scope, receive, send) -> None:  # noqa: ARG001, ANN001
        seen_trace["value"] = current_trace_id.get("")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message: dict) -> None:
        pass

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    middleware = TracingMiddleware(inner_app)
    headers = [(b"x-trace-id", b"this-is-way-too-long-for-our-format")]
    await middleware(_scope(headers), receive, send)

    assert seen_trace["value"] == "this-is-"  # truncated to 8 chars


@pytest.mark.unit
async def test_middleware_skips_non_http_scopes() -> None:
    """Lifespan/websocket scopes must pass through untouched."""
    inner_called = False

    async def inner_app(scope, receive, send) -> None:  # noqa: ARG001, ANN001
        nonlocal inner_called
        inner_called = True

    async def send(message: dict) -> None:
        pass

    async def receive() -> dict:
        return {}

    middleware = TracingMiddleware(inner_app)
    await middleware({"type": "lifespan"}, receive, send)
    assert inner_called
