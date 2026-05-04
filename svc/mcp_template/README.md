# mcp_template

Minimal MCP service used as a copy-paste starting point. Exposes two tools:

- `echo(message)` — returns the message verbatim
- `ping()` — returns `"ok"`

## Run locally

```bash
APP_MCP_AUTH_ENABLED=false APP_JSON_FORMAT_LOGS=true \
  python bin/run_service.py mcp_template
```

Then call it:

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Conversation-Id: demo-conv-1" \
  -H "X-Interaction-Id: demo-int-1" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

The log line for the tool call will carry `application=mcp-template`,
`conversation_id=demo-conv-1`, `interaction_id=demo-int-1`, and a generated `trace_id`.
