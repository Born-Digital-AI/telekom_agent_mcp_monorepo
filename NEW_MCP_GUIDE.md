# Adding a new MCP server

This guide walks through adding a brand-new MCP server. Use [`svc/mcp_template/`](svc/mcp_template/) as a starting point.

## 1. Create the service directory

```
svc/mcp_<your_name>/
├── __init__.py        # Service class (MCPService subclass) + SERVICE_CLASS export
├── __main__.py        # `python -m svc.mcp_<your_name>`
├── requirements.in    # Local + 3rd-party dependencies
├── tools/             # (or tools.py)  MCP tool implementations
└── README.md
```

Use **underscores** in the directory name (Python module convention). The K8s/Docker
`NAME` will use **dashes** (e.g. `mcp-your-name`).

## 2. Service class skeleton

```python
"""MyMCP service — short description."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pydantic
from mcp.types import ToolAnnotations

from lib.mcp_service import MCPService, MCPServiceConfig

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MyMCPConfig(MCPServiceConfig):
    mcp_name: str = "mcp-your-name"
    # add service-specific options here, e.g. external API URL, timeouts


class MyMCP(MCPService[MyMCPConfig]):
    NAME = "mcp-your-name"           # K8s / Docker / `application` log field
    TEAM = "your-team"                # used in K8s labels

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
        async def my_tool(
            arg: Annotated[str, pydantic.Field(description="What this argument is for")],
        ) -> str:
            """One-line summary used by the LLM."""
            self.logger.info(f"my_tool called with arg={arg!r}")
            return arg


SERVICE_CLASS = MyMCP
```

## 3. requirements.in

```
-r ../../lib/mcp_service/requirements.in

# Add 3rd-party packages here, e.g.
# httpx
# pydantic-extra-types
```

## 4. Run locally

```bash
APP_LOGSTASH_ENABLED=false APP_JSON_FORMAT_LOGS=true APP_MCP_AUTH_ENABLED=false \
  python bin/run_service.py mcp_<your_name>
```

Verify a sample MCP request:

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "X-Conversation-Id: dev-1" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'
```

The first log line should contain `application=mcp-your-name` and `conversation_id=dev-1`.

## 5. Add to CI

Open [`.github/workflows/build_and_push_one.yml`](.github/workflows/build_and_push_one.yml)
and add your service to the `service_name.options` list. (`build_and_push_all.yml`
auto-detects services from `svc/`.)

## 6. Migrating tools from `my-mcp-server`

If you're porting a project from the legacy `my-mcp-server` repo, you can keep the existing
`tools.py` mostly untouched. Replace just the imports:

```python
# old:
# from mcp_server import ToolRegistry, mcp_tool
# from customer_db import find_by_phone

# new:
from lib.mcp_service.legacy_compat import ToolRegistry, mcp_tool
from .customer_db import find_by_phone
```

Drop the `sys.path.insert(0, str(_project_dir))` block — it's not needed under the
service-per-directory layout.

The compat shim:

- Strips the `_meta` parameter from the MCP-visible signature.
- Auto-injects `_meta = {"conversation_id", "interaction_id", "trace_id"}` from the
  ContextVars populated by `lib.mcp_service.middleware.TracingMiddleware`.
- Ignores `input_schema=` (FastMCP derives the schema from the function signature).

The service `__init__.py` then plugs the legacy `register(registry)` function into FastMCP:

```python
from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry
from .tools import register

class MyService(MCPService[MCPServiceConfig]):
    NAME = "mcp-your-name"
    TEAM = "your-team"

    def setup_tools(self, mcp):
        registry = ToolRegistry(mcp)
        register(registry)
```
