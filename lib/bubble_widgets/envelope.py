"""Da-Bubble widget transport envelope.

A tool that wants the host to render a widget (instead of plain text) returns
``bubble_widget_result(...)``. The ``type: "bubble_widget_result"`` marker is the
signal to the client that the payload is a widget tree, not an assistant turn.
"""

from __future__ import annotations


def bubble_widget_result(
    summary: str,
    widget: dict[str, object],
    template: str,
    assistant_text: str = "",
) -> dict[str, object]:
    """Wrap a Da-Bubble widget tree in the canonical transport envelope.

    - ``summary`` — short technical description (for logs / tool result text).
    - ``widget`` — the nested Da-Bubble component tree (dict).
    - ``template`` — widget type id used by the renderer for routing.
    - ``assistant_text`` — optional assistant-facing text shown alongside the widget.
    """
    result: dict[str, object] = {
        "type": "bubble_widget_result",
        "template": template,
        "summary": summary,
        "widget": widget,
    }
    assistant_text_value = assistant_text.strip()
    if assistant_text_value:
        result["assistant_text"] = assistant_text_value
    return result
