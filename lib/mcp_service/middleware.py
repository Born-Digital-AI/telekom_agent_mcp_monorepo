"""ASGI middleware that binds MCP request context (trace/conversation/interaction IDs).

FastMCP's streamable-http transport exposes a Starlette ASGI app, but unlike `lib/web_service`
it doesn't bring its own header-extracting middleware. This module provides one so that
`conversation_id` / `interaction_id` / `trace_id` flow into log records emitted while a tool runs.

Usage (inside ``MCPService.run_forever``):

    from lib.mcp_service.middleware import wrap_with_tracing
    app = mcp.streamable_http_app()
    app = wrap_with_tracing(app)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.boilerplate.logging import (
    CONVERSATION_ID_HEADER,
    INTERACTION_ID_HEADER,
    TRACE_HEADER,
    TRACE_ID_LENGTH,
    current_trace_id,
    random_trace_id,
    set_conversation_id,
    set_interaction_id,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, MutableMapping
    from typing import Any

    Scope = MutableMapping[str, Any]
    Message = MutableMapping[str, Any]
    Receive = Callable[[], Awaitable[Message]]
    Send = Callable[[Message], Awaitable[None]]
    ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _header(headers: list[tuple[bytes, bytes]], name: str) -> str:
    """Look up an HTTP header in the ASGI scope's headers list."""
    name_bytes = name.lower().encode("latin-1")
    for key, value in headers:
        if key == name_bytes:
            return value.decode("latin-1", errors="replace")
    return ""


class TracingMiddleware:
    """Bind trace/conversation/interaction IDs from request headers into ContextVars.

    Wraps an ASGI app and runs only on HTTP scopes. For each request:

    1. Reads ``X-Trace-Id`` (or generates a fresh random one), trims to ``TRACE_ID_LENGTH``,
       and sets ``current_trace_id``.
    2. Reads ``X-Conversation-Id`` and ``X-Interaction-Id`` (empty string if absent) and
       sets the matching ContextVars.
    3. Echoes ``X-Trace-Id`` back on the response so callers can correlate.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the middleware around the wrapped ASGI app."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = scope.get("headers", [])

        trace_id = _header(headers, TRACE_HEADER) or random_trace_id()
        if len(trace_id) > TRACE_ID_LENGTH:
            trace_id = trace_id[:TRACE_ID_LENGTH]
        current_trace_id.set(trace_id)

        set_conversation_id(_header(headers, CONVERSATION_ID_HEADER))
        set_interaction_id(_header(headers, INTERACTION_ID_HEADER))

        async def send_with_trace(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (TRACE_HEADER.encode("latin-1"), trace_id.encode("latin-1")),
                )
                message["headers"] = response_headers
            await send(message)

        await self.app(scope, receive, send_with_trace)


def wrap_with_tracing(app: ASGIApp) -> ASGIApp:
    """Return ``app`` wrapped with :class:`TracingMiddleware`."""
    return TracingMiddleware(app)
