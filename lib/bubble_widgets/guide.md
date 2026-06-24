# Building widget tools (Da-Bubble)

A **widget tool** is an MCP tool whose purpose is to send an interactive visual
element into a **chat** conversation — a form, a selector, a confirmation — instead
of replying with plain text. Widgets are visual, support pattern validation, and
(crucially) keep sensitive data out of the LLM: the customer types into the widget,
the values land in `named_entities`, and our tools read them from there.

## When to render a widget vs. ask for text

A widget can only render in a **chat** channel. Gate on the conversation's
`named_entities`:

- `Channel == "chat"` → you may return a widget.
- anything else (or `Channel` absent) → **text mode**: the LLM must collect the
  input and pass it as a tool parameter. Default to text mode when unsure — a
  widget that can't render is worse than a question.

## The round-trip (privacy-safe submit)

```
tool()  ──renders──▶  widget (Form + Input + submit)
                         │ customer fills + submits
                         ▼
host: writes field values into named_entities
host: emits ONLY a hidden utterance (e.g. "identifikacia_widget_submitted")
                         ▼
LLM reacts to the utterance ──calls──▶  tool()
                         │ reads field values from named_entities (NOT from params)
                         ▼
                      result
```

The raw values never enter the LLM turn. The widget's `Input.name` **is** the
`named_entities` key the tool reads — keep them in lock-step.

## Transport

Return `bubble_widget_result(summary, widget, template, assistant_text)` from
`lib.bubble_widgets`, JSON-serialised. The `type: "bubble_widget_result"` marker
tells the host to render `widget` rather than speak it. `assistant_text` is the
optional spoken line shown next to the widget.

## Submit

Attach `hidden_submit_action("<utterance>")` (from `lib.bubble_widgets`) as the
submit `Button`'s `onClickAction`. Never hand-roll the `as_buttons` payload.

## Authoring rules

- One tool per business use-case (or one tool with a small variant enum). Do not
  expose a generic `render_widget(widget_json)` tool.
- Author the widget tree in deterministic Python, in the **owning service**
  (e.g. `svc/<service>/widgets.py`), not in this shared lib.
- Validate business inputs at the tool boundary; the widget's `pattern` is a UX
  convenience, server-side validation is the source of truth.
- Keep the Telekom house style (magenta `#E20074`, input radius `15px`).

## Supported component families (summary)

Layout: `Box` `Grid` `Row` `Col` `Card` `Spacer` `Divider` ·
Controls: `Form` `FormControl` `Button` `Input` `Textarea` `Select` `Tabs`
`Checkbox` `Radio` `RadioGroup` `DatePicker` ·
Typography: `Text` `Title` `Caption` `Label` ·
Content: `Image` `Icon` `Badge` `Tooltip` · Functional: `ArrayOf` `Condition` `SideEffect`

Exact props live in the Da-Bubble widget-builder spec
(`https://development.borndigital.ai/widget-builder`). When a component's prop
shape is uncertain, centralise its construction in one helper so a spec change is
a one-line fix.
