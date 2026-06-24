"""Shared helpers for building Da-Bubble widgets returned by MCP tools.

A "widget" is a tool whose purpose is to render an interactive visual element
into a chat conversation (input forms, selectors, …) instead of plain text. Two
things matter and live here because they are identical across every service:

- :func:`bubble_widget_result` — the transport envelope the host recognises.
- :func:`hidden_submit_action` — privacy-safe submit (values land in
  ``named_entities``, only a hidden utterance reaches the LLM).

Service-specific widget *trees* (which fields, labels, styling) are authored in
the owning service, not here. See ``svc/mcp_telekom_identity/widgets.py`` for an
example and ``guide.md`` for the authoring rules.
"""

from __future__ import annotations

from .actions import hidden_submit_action
from .envelope import bubble_widget_result

__all__ = [
    "bubble_widget_result",
    "hidden_submit_action",
]
