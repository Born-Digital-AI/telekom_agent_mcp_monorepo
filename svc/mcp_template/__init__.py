"""Template MCP service used as a copy-paste starting point and sanity check."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import pydantic
from mcp.types import ToolAnnotations

from lib.mcp_service import MCPService, MCPServiceConfig

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTemplateConfig(MCPServiceConfig):
    """Configuration for the template MCP service."""

    mcp_name: str = "mcp-template"


class MCPTemplate(MCPService[MCPTemplateConfig]):
    """Minimal MCP service that exposes a single ``echo`` tool."""

    NAME = "mcp-template"
    TEAM = "platform"

    CPU_REQUEST = "50m"
    MEMORY_REQUEST = "128Mi"
    CPU_LIMIT = "500m"
    MEMORY_LIMIT = "256Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register the demo tools on the FastMCP instance."""
        read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True)

        @mcp.tool(annotations=read_only)
        async def echo(
            message: Annotated[str, pydantic.Field(description="Text to echo back")],
        ) -> str:
            """Return the message verbatim. Used to verify the server is reachable."""
            self.logger.info(f"echo tool invoked with message={message!r}")
            return message

        @mcp.tool(annotations=read_only)
        async def ping() -> str:
            """Health probe; always returns 'ok'."""
            return "ok"


SERVICE_CLASS = MCPTemplate
