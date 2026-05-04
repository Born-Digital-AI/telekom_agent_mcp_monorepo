"""Compatibility shim for tools written against the old ``my-mcp-server`` API.

Tools migrated from ``my-mcp-server`` use::

    from mcp_server import ToolRegistry, mcp_tool

    def register(registry: ToolRegistry) -> None:
        @mcp_tool(name=..., description=..., registry=registry)
        def my_tool(arg: str, _meta: dict | None = None) -> str: ...

This module provides drop-in replacements for ``ToolRegistry`` and ``mcp_tool``
that route registrations to a FastMCP instance and inject ``_meta`` from the
ContextVars populated by :class:`lib.mcp_service.middleware.TracingMiddleware`.

The wrapper:

- Hides the ``_meta`` parameter from the MCP schema (so the LLM doesn't see it)
  while still passing it to the underlying function.
- Preserves async-ness — ``async def`` tools produce an awaitable wrapper, sync
  tools stay sync.
- Ignores ``input_schema=`` (FastMCP derives the schema from the function
  signature; the old manual override is no-op).

Migration workflow (legacy ``my-mcp-server/projects/<name>/``)
--------------------------------------------------------------

1. Copy the project's ``.py`` files into ``svc/mcp_<name>/``.
2. In ``tools.py`` swap two imports — ``mcp_server`` → this module, and any
   sibling imports to relative form::

        # before:
        # from mcp_server import ToolRegistry, mcp_tool
        # from customer_db import find_by_phone

        # after:
        from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
        from .customer_db import find_by_phone

3. Drop the legacy sys-path bootstrap (no longer needed under per-service layout)::

        # remove:
        # _project_dir = Path(__file__).parent
        # if str(_project_dir) not in sys.path:
        #     sys.path.insert(0, str(_project_dir))

4. Add ``svc/mcp_<name>/__init__.py`` that plugs ``register(registry)`` into
   FastMCP::

        from lib.mcp_service import MCPService, MCPServiceConfig
        from lib.mcp_service.legacy_compat import ToolRegistry
        from .tools import register

        class MCPMyService(MCPService[MCPServiceConfig]):
            NAME = "mcp-my-service"
            TEAM = "your-team"

            def setup_tools(self, mcp):
                registry = ToolRegistry(mcp)
                register(registry)

        SERVICE_CLASS = MCPMyService

5. Drop legacy artefacts that don't apply: ``project.json`` (replaced by config
   class), ``startup.py`` (move logic into ``__init__``), ``web.py`` (custom
   HTTP routes — re-implement via ``MCPService.register_http_endpoints`` if
   actually needed).

Worked example: :mod:`svc.mcp_telekom_cc_selfcare`.
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
    - Preserves async-ness: an ``async def`` ``func`` produces an ``async`` wrapper
      so FastMCP awaits the coroutine. A sync ``func`` produces a sync wrapper.
    """
    accepts_meta = "_meta" in inspect.signature(func).parameters

    def _inject_meta(kwargs: dict[str, Any]) -> None:
        kwargs.pop("_meta", None)
        if accepts_meta:
            kwargs["_meta"] = _build_meta()

    wrapped: Callable[..., Any]
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapped(**kwargs: Any) -> Any:  # noqa: ANN401
            _inject_meta(kwargs)
            return await func(**kwargs)

        wrapped = async_wrapped
    else:

        @functools.wraps(func)
        def sync_wrapped(**kwargs: Any) -> Any:  # noqa: ANN401
            _inject_meta(kwargs)
            return func(**kwargs)

        wrapped = sync_wrapped

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
