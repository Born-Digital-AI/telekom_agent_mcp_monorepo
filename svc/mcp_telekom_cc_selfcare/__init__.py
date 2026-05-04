"""Telekom CC Selfcare MCP service (authentication + invoice resend)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomCcSelfcareConfig(MCPServiceConfig):
    """Configuration for the Telekom CC Selfcare service."""

    mcp_name: str = "mcp-telekom-cc-selfcare"


class MCPTelekomCcSelfcare(MCPService[MCPTelekomCcSelfcareConfig]):
    """Customer-care selfcare flow: authentication and invoice resend."""

    NAME = "mcp-telekom-cc-selfcare"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def __init__(self, config: MCPTelekomCcSelfcareConfig) -> None:
        super().__init__(config)

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register the CC Selfcare tools onto FastMCP via the legacy registry adapter."""
        registry = ToolRegistry(mcp)
        register(registry)


SERVICE_CLASS = MCPTelekomCcSelfcare
