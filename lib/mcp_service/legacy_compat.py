"""Compatibility shim for tools written against the old ``my-mcp-server`` API.

Tools migrated from ``my-mcp-server`` use::

    from mcp_server import ToolRegistry, mcp_tool

    def register(registry: ToolRegistry) -> None:
        @mcp_tool(name=..., description=..., registry=registry)
        def my_tool(arg: str, _meta: dict | None = None) -> str: ...

This module provides drop-in replacements for ``ToolRegistry`` and ``mcp_tool``
that route registrations to a FastMCP instance and inject ``_meta`` from the
ContextVars populated by :class:`lib.mcp_service.middleware.TracingMiddleware`.

The wrapper hides the ``_meta`` parameter from the MCP schema (so the LLM
doesn't see it) while still passing it to the underlying function.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

from lib.boilerplate.logging import (
    current_conversation_id,
    current_interaction_id,
    current_trace_id,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP


class ToolRegistry:
    """Drop-in replacement for the legacy registry. Holds a FastMCP reference."""

    def __init__(self, mcp: FastMCP) -> None:
        self.mcp = mcp


def _build_meta() -> dict[str, str]:
    """Snapshot of the request-scoped IDs that legacy tools consume via ``_meta``."""
    return {
        "conversation_id": current_conversation_id.get(""),
        "interaction_id": current_interaction_id.get(""),
        "trace_id": current_trace_id.get(""),
    }


def _strip_meta_signature(func: Callable[..., Any]) -> inspect.Signature:
    """Return ``func``'s signature with the ``_meta`` parameter removed.

    FastMCP derives the MCP tool schema from this signature, so the LLM only
    sees the real domain parameters.
    """
    sig = inspect.signature(func)
    params = [p for name, p in sig.parameters.items() if name != "_meta"]
    return sig.replace(parameters=params)


def _wrap_for_fastmcp(func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap ``func`` so it can be registered with ``@mcp.tool``.

    - Removes ``_meta`` from the visible signature.
    - Injects a fresh ``_meta`` dict on every call from the active ContextVars.
    """
    accepts_meta = "_meta" in inspect.signature(func).parameters

    @functools.wraps(func)
    def wrapped(**kwargs: Any) -> Any:  # noqa: ANN401
        # Never trust an explicit `_meta` from the caller — always overwrite.
        kwargs.pop("_meta", None)
        if accepts_meta:
            kwargs["_meta"] = _build_meta()
        return func(**kwargs)

    wrapped.__signature__ = _strip_meta_signature(func)  # type: ignore[attr-defined]
    if hasattr(wrapped, "__annotations__"):
        wrapped.__annotations__.pop("_meta", None)
    return wrapped


def mcp_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    input_schema: dict[str, Any] | None = None,  # noqa: ARG001 — accepted for API compat, ignored
    registry: ToolRegistry | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Drop-in replacement for the legacy ``@mcp_tool`` decorator.

    When ``registry`` is provided, the decorated function is registered on the
    underlying FastMCP. ``input_schema`` is accepted for API compatibility but
    ignored — FastMCP derives the schema from the function signature.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if registry is None:
            return func
        wrapped = _wrap_for_fastmcp(func)
        registry.mcp.tool(name=name or func.__name__, description=description)(wrapped)
        return func

    return decorator
