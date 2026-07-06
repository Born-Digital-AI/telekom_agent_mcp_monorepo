# AGENTS.md

Conventions for AI agents (and humans) adding or modifying MCP servers in this monorepo.
Read this before touching code. The rules are deliberately tight so every new server
ships with consistent observability, security, and ops behavior.

## Cardinal rules

1. **One service per MCP server.** Each MCP server lives in its own `svc/mcp_<name>/`
   directory and produces its own Docker image. Never bundle multiple MCPs into one service.
2. **Subclass `MCPService[ConfigClass]`** from `lib.mcp_service`. Never instantiate
   `FastMCP` directly outside this class hierarchy — you would bypass logging, healthz,
   auth, and the tracing middleware.
3. **Don't reconfigure logging.** The root logger is set up by `bin/run_service.py` →
   `configure_logging()`. Just call `self.logger.info(...)` and the right structured
   fields appear automatically.
4. **Don't import across services.** `svc/mcp_a/` cannot import from `svc/mcp_b/`.
   Shared helpers go in `lib/`. Enforced by `bin/check_imports.py` (pre-commit).
5. **All env vars use the `APP_` prefix** and are declared as fields on a Pydantic
   `Config` class. Never read `os.environ` directly inside a service for configuration.
6. **Verify before claiming done** — run the verification commands at the bottom of
   this file. CI will reject the PR if any of them fails.

## Repo layout

```
lib/
├── boilerplate/      Service base class, ServiceConfig, structured logging
├── healthz/          /healthz endpoint (managed resource)
├── mcp_service/      FastMCP wrapper (MCPService, MCPServiceConfig, MCPAuth)
│   ├── middleware.py      ASGI tracing middleware (binds X-Trace/Conversation/Interaction-Id)
│   └── legacy_compat.py   Compatibility shim for my-mcp-server tools
└── monorepo/         Filesystem helpers used by bin/ and CI

svc/<name>/           One MCP server per directory
bin/                  Entry points: run_service.py, upgrade_packages.py, check_imports.py
docker/               service.Dockerfile (parameterised by service_name build-arg)
tests/lib|svc/        Mirrors the source tree
```

Reference example: [`svc/mcp_template/`](svc/mcp_template/) — the canonical minimal service.
Pattern for migrated legacy code: [`svc/mcp_telekom_identity/`](svc/mcp_telekom_identity/)
(uses the `lib.mcp_service.legacy_compat` shim).

## Adding a new MCP server

### 1. Choose names

| Form | Example | Used for |
|---|---|---|
| Directory name (snake_case) | `mcp_my_service` | `svc/<this>/`, Python module path |
| Class `NAME` (kebab-case) | `mcp-my-service` | K8s deployment, Docker image, `application` log field |
| Class name (PascalCase) | `MCPMyService` | Python class in `__init__.py` |

The class `NAME` must match the directory name with underscores → dashes.

### 2. Create the directory layout

```
svc/mcp_my_service/
├── __init__.py        # Service class + SERVICE_CLASS export
├── __main__.py        # Allows `python -m svc.mcp_my_service`
├── requirements.in    # Local + 3rd-party deps
├── tools.py           # (or tools/ package) — MCP tool implementations
└── README.md          # What this service does, run instructions
```

### 3. Service class skeleton (copy this verbatim)

```python
"""MyService — short description of what this MCP server does."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pydantic
from mcp.types import ToolAnnotations

from lib.mcp_service import MCPService, MCPServiceConfig

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPMyServiceConfig(MCPServiceConfig):
    """Configuration for MyService."""

    mcp_name: str = "mcp-my-service"
    # Add service-specific options here, e.g.:
    # external_api_url: str = "https://api.example.com"
    # external_api_key: str = pydantic.Field(default="", exclude=True)  # secret


class MCPMyService(MCPService[MCPMyServiceConfig]):
    """One-line summary of what this server does."""

    NAME = "mcp-my-service"
    TEAM = "your-team"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
        async def my_tool(
            arg: Annotated[str, pydantic.Field(description="What this argument means")],
        ) -> str:
            """One-line summary the LLM will see."""
            self.logger.info(f"my_tool called with arg={arg!r}")
            return arg


SERVICE_CLASS = MCPMyService
```

The `__init__` override and the `SERVICE_CLASS = ...` export are both mandatory.

### 4. `__main__.py` (copy verbatim, just rename)

```python
"""Allow `python -m svc.mcp_my_service` to run the service directly."""

from __future__ import annotations

import asyncio

from bin.run_service import run_service

if __name__ == "__main__":
    asyncio.run(run_service("mcp_my_service"))
```

### 5. `requirements.in`

```
# Direct local dependencies
-r ../../lib/mcp_service/requirements.in

# Direct 3rd-party dependencies (one per line, unpinned — uv resolves them)
# httpx
# pydantic-extra-types
```

### 6. Add to CI

Edit [`.github/workflows/build_and_push_one.yml`](.github/workflows/build_and_push_one.yml)
and add the directory name to the `service_name.options` dropdown.
`build_and_push_all.yml` auto-detects services from `svc/` — no edit needed there.

## Logging contract — what's automatic, what to do, what NOT to do

Every log record emitted via `self.logger` (or any logger) automatically carries:

| Field | Source |
|---|---|
| `application` / `service.name` | Your `NAME` class attribute |
| `app_version` / `service.version` | `APP_GIT_COMMIT` env var |
| `trace.id` / `trace_id` | `X-Trace-Id` header (or random 8-char fallback) |
| `conversation_id` / `ConversationId` | `X-Conversation-Id` header |
| `interaction_id` / `InteractionId` | `X-Interaction-Id` header |

These flow through `lib.mcp_service.middleware.TracingMiddleware`, which is wrapped
around the FastMCP ASGI app inside `MCPService.run_forever()`. You don't need to do
anything for them to work.

When `APP_LOGSTASH_ENABLED=true` (default in [.env.example](.env.example)) the same
records are shipped to Logstash → Kibana via `python-logstash-async`.

### DO

- Use `self.logger.info(...)`, `self.logger.warning(...)`, `self.logger.exception(...)`.
- Log enough to trace a request end-to-end: tool name, key inputs, key outcomes.
- Log a single line per business event, structured by `repr()` or short kwargs.

### DO NOT

- **Don't call `logging.basicConfig`, `logger.addHandler`, or replace the formatter.**
  You will break the Logstash handler and the context-var injectors.
- **Don't use `print(...)`** in service code. Print bypasses the structured pipeline
  and is invisible in Kibana.
- **Don't manually call `set_conversation_id` / `set_interaction_id` / `set_trace_id`**
  inside tool code. The middleware has already populated them.
- **Don't read these IDs by parsing request headers yourself** — read them from the
  ContextVars: `current_conversation_id.get("")` etc. (`from lib.boilerplate.logging import ...`).
- **Don't log secrets.** API keys belong in `pydantic.Field(default=..., exclude=True)`
  config fields and never appear in `repr(config)`.
- **Don't log full PII.** For Telekom servers, names/birth-numbers/full phone numbers
  must be masked — log only a suffix (e.g. `customer_id_last4` in
  [`mcp_telekom_identity/tools.py`](svc/mcp_telekom_identity/tools.py)).

## Tool authoring rules

- **Decorator:** `@mcp.tool(annotations=ToolAnnotations(readOnlyHint=..., idempotentHint=...))`.
  Always set `readOnlyHint` honestly — LLM clients route around it.
- **Parameter types:** `Annotated[T, pydantic.Field(description="...")]`. The description
  goes into the MCP schema and is what the LLM sees.
- **Return type:** `str`. Serialize complex outputs with `json.dumps(obj, ensure_ascii=False)`.
- **Async vs sync:** prefer `async def` for I/O tools. Sync is fine for pure computation
  and is preserved by the legacy compat shim.
- **Tool docstring:** the first line is the LLM-facing one-liner. Keep it imperative
  and specific (e.g. "Resend the customer's invoice to their registered email.").
- **Don't accept `Context` parameter unless you genuinely need it.** Most metadata
  flows through ContextVars; pulling it from `Context` makes the schema noisier.

### Reading conversation/interaction context from inside a tool

If you need to branch on the active conversation:

```python
from lib.boilerplate.logging import current_conversation_id, current_interaction_id

@mcp.tool(...)
async def my_tool(arg: str) -> str:
    conversation_id = current_conversation_id.get("")
    interaction_id = current_interaction_id.get("")
    ...
```

Both will be empty strings if the caller didn't send the headers — branch defensively.

## State management

Services that need transient per-conversation state (auth progress, multi-step
flows) should use `lib.mcp_service.state.TTLStore` rather than a plain `dict`:

```python
from lib.mcp_service.state import TTLStore

_AUTH_STATE: TTLStore[dict[str, Any]] = TTLStore(ttl_seconds=30 * 60)
```

`TTLStore` evicts entries that haven't been written for `ttl_seconds`. This
prevents the unbounded-memory growth a plain `dict` keyed on conversation_id
suffers in production.

**Single-replica only.** `TTLStore` is process-local. If a service uses it,
the K8s deployment must run `replicas=1`. For multi-replica scale-out,
replace it with a Redis (or similar) shared store.

## Configuration rules

- All env vars use the `APP_` prefix. Pydantic Settings wires them up automatically.
- Secrets: declare with `pydantic.Field(default="", exclude=True)`. They are then
  masked in `repr(config)` and never logged.
- Required values: declare without a default — the service will refuse to start
  if the env var is missing.
- Don't add new config to `lib/mcp_service/__init__.py::MCPServiceConfig` for
  service-specific options. Subclass it inside your service.

Service-specific config example:

```python
class MCPMyServiceConfig(MCPServiceConfig):
    mcp_name: str = "mcp-my-service"
    upstream_api_url: str = "https://api.example.com"
    upstream_api_timeout: float = 5.0
    upstream_api_key: str = pydantic.Field(default="", exclude=True)  # APP_UPSTREAM_API_KEY
```

## Auth

Off by default (`mcp_auth_enabled=False`). To turn it on for a service, set in env:

```
APP_MCP_AUTH_ENABLED=true
APP_MCP_AUTH_API_KEY=<bearer-token>
```

The bearer token must then be sent as `Authorization: Bearer <token>`. Implementation
is in `MCPAuth` ([`lib/mcp_service/__init__.py`](lib/mcp_service/__init__.py)).
Don't roll your own auth — extend `MCPAuth` if you need a different scheme.

## Testing

Mirror the source tree under `tests/`:

```
tests/lib/test_<module>.py            # tests for lib/
tests/svc/<service>/test_<module>.py  # tests for that service
```

Mark unit tests with `@pytest.mark.unit`. Component tests requiring infrastructure
go under `@pytest.mark.component` (excluded from default run). See
[`tests/lib/test_mcp_middleware.py`](tests/lib/test_mcp_middleware.py) for the pattern.

For tools that hit external APIs (NLP engine, Daktela, etc.), mock with `pytest-mock`.
Don't write tests that require live external services in `unit`.

## Pre-merge verification (run all of these)

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/basedpyright
.venv/bin/python bin/check_imports.py
.venv/bin/pytest -m unit
```

For service-specific changes, also smoke-test the server boots and accepts a request:

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
APP_MCP_PORT=8765 APP_HEALTHZ_PORT=8766 APP_COLLECT_METRICS=false \
  .venv/bin/python bin/run_service.py mcp_my_service &

sleep 3
curl -s -X POST http://localhost:8765/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Conversation-Id: smoke-1" \
  -H "X-Interaction-Id: smoke-2" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' | head -c 500
```

The response must be HTTP 200 with a `tools/list` JSON-RPC payload, and the server's
log line for the call must include `application=mcp-my-service`,
`conversation_id=smoke-1`, `interaction_id=smoke-2`.

## Code style — non-obvious things

- `from __future__ import annotations` is enforced in every Python file (ruff `I001`).
- Line length is 100 (ruff `line-length = 100`). Don't break long string literals.
- Use modern type syntax: `str | None` not `Optional[str]`, `dict[str, int]` not
  `Dict[str, int]`.
- Don't add useless docstrings (`"""Initialize."""` etc). The codebase prefers a short
  `__init__.py` module docstring and informative class/function docstrings only.
- Don't add comments that restate the code (`# loop over items`). Comments explain
  *why*, not *what*.
- Per-file ruff overrides for legacy migrated code live in
  [`pyproject.toml`](pyproject.toml) under `[tool.ruff.lint.per-file-ignores]`.
  Add new entries there rather than peppering `# noqa` comments through the code.

## When you're stuck

- Mirror an existing service that's similar in shape. The 5 services in `svc/` cover
  the common patterns.
- Check the original `python-monorepo` (path: `/Users/michaljurco/Documents/GitHub/python-monorepo`)
  for richer examples of `lib/boilerplate` usage — that repo is the upstream source
  for everything in `lib/` here.
- Don't invent new patterns when an existing one works. The compat shim was added
  precisely so legacy tools can move 1:1 without rewriting business logic.
