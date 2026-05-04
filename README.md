# telekom_agent_mcp_monorepo

Monorepo for **Model Context Protocol (MCP) servers** consumed by LLM-based agents.

The repo follows the conventions of the production `python-monorepo`:

- One service per MCP server (`svc/<name>/`), each producing its own Docker image.
- Shared infrastructure (`lib/boilerplate`, `lib/healthz`, `lib/mcp_service`) is reused across all services.
- MCP servers are built on **FastMCP** (the official `mcp` Python package), not a custom JSON-RPC implementation.
- Logs are shipped to **Kibana via Logstash** with `application` (service name), `conversation_id`, `interaction_id`, and `trace_id` fields populated from request headers.

## Repository layout

```
.
├── bin/                 Entry points and tooling (run_service.py, upgrade_packages.py, …)
├── docker/              service.Dockerfile (parameterised by service name)
├── lib/
│   ├── boilerplate/     Service base class, Pydantic config, structured logging
│   ├── healthz/         /healthz endpoint
│   ├── mcp_service/     FastMCP wrapper + ASGI tracing middleware + legacy compat shim
│   └── monorepo/        Filesystem helpers used by bin/ scripts and CI
├── svc/
│   ├── mcp_template/                  Reference example
│   ├── mcp_telekom_cc_selfcare/       Authentication + invoice resend
│   └── mcp_telekom_thd_selfcare/      Fixed internet troubleshooting
├── tests/
├── requirements/        Generated pinned requirements (committed)
├── pyproject.toml       Ruff / basedpyright / pytest config
├── .pre-commit-config.yaml
├── .env.example
└── .github/workflows/   CI + per-service Docker build & push
```

## Adding tools

Tools live inside the service directory. The recommended pattern (see [svc/mcp_template/__init__.py](svc/mcp_template/__init__.py)) uses FastMCP decorators directly:

```python
from mcp.types import ToolAnnotations
from lib.mcp_service import MCPService, MCPServiceConfig

class MyService(MCPService[MCPServiceConfig]):
    NAME = "mcp-my-service"
    TEAM = "platform"

    def setup_tools(self, mcp):
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
        async def my_tool(arg: str) -> str:
            return f"got {arg}"

SERVICE_CLASS = MyService
```

For tools migrated from the legacy `my-mcp-server` repo, use the compatibility shim
(see [svc/mcp_telekom_cc_selfcare/](svc/mcp_telekom_cc_selfcare/)):

```python
from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool

def register(registry):
    @mcp_tool(name="...", description="...", registry=registry)
    def my_tool(arg: str, _meta: dict | None = None) -> str:
        # _meta is auto-populated from the active conversation/interaction context.
        return ...
```

## Running locally

```bash
uv venv
uv pip install -r requirements-dev.in
shopt -s nullglob; for r in svc/*/requirements.in; do uv pip install -r "$r"; done

# Run the template service (no auth, JSON logs, Logstash off)
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
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

The log line for the call carries `application=mcp-template`, `conversation_id=demo-conv-1`, `interaction_id=demo-int-1`, and a generated `trace_id`.

## Logging → Kibana

`lib/boilerplate/logging.py` configures the root logger with three filters that inject context fields into every record:

| Field | Source |
|---|---|
| `trace.id` / `trace_id` | `X-Trace-Id` request header (or generated). 8-char random fallback. |
| `service.name` / `application` | `Service.NAME` class attribute (e.g. `mcp-telekom-cc-selfcare`). |
| `service.version` / `app_version` | `APP_GIT_COMMIT` env var (set in Dockerfile from `git_commit` build-arg). |
| `conversation_id` / `ConversationId` | `X-Conversation-Id` request header. |
| `interaction_id` / `InteractionId` | `X-Interaction-Id` request header. |

When `APP_LOGSTASH_ENABLED=true` and `APP_LOGSTASH_HOST` / `APP_LOGSTASH_PORT` are set, an `AsynchronousLogstashHandler` ships the same records to Logstash → Kibana. Defaults for the dev cluster are in [.env.example](.env.example) (`10.4.0.6:5959`).

The MCP HTTP transport doesn't carry these headers natively — that's wired up by [lib/mcp_service/middleware.py](lib/mcp_service/middleware.py), an ASGI middleware that runs before each request.

## Building and deploying

GitHub Actions provides:

- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — ruff, basedpyright, import direction check, pytest on PRs.
- [`.github/workflows/build_and_push_one.yml`](.github/workflows/build_and_push_one.yml) — manual per-service Docker build & push to Docker Hub.
- [`.github/workflows/build_and_push_all.yml`](.github/workflows/build_and_push_all.yml) — manual or release-triggered build for every service.

Kubernetes deployment is handled by separate repos (`non-prod-kubernetes`, `aks-prod-kubernetes`) — this repo only produces images.

## Adding a new MCP server

See [AGENTS.md](AGENTS.md) — the conventions and step-by-step skeleton are
maintained there for both human contributors and AI coding agents. For
porting tools from the legacy `my-mcp-server` repo, the migration recipe lives
in the [`lib/mcp_service/legacy_compat.py`](lib/mcp_service/legacy_compat.py)
module docstring.
