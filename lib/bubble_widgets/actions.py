"""Privacy-safe widget submit actions.

A submit button carries an ``onClickAction`` of type ``as_buttons`` with a
hidden utterance. On submit the host:

1. writes the widget field values into the conversation's ``named_entities``
   (so server-side tools can read them), and
2. emits only the hidden technical utterance into the conversation.

The raw field values therefore never enter the LLM turn — the LLM sees only the
utterance and reacts by calling the matching tool, which reads the values from
``named_entities``.
"""

from __future__ import annotations


def hidden_submit_action(utterance: str) -> dict[str, object]:
    """Return an ``as_buttons`` action that emits ``utterance`` as a hidden message."""
    return {
        "type": "as_buttons",
        "payload": {
            "utterance": utterance,
            "hidden": True,
        },
    }
