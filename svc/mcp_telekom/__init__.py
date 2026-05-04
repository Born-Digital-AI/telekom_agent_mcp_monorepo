"""Telekom intent recognition MCP service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomConfig(MCPServiceConfig):
    """Configuration for the Telekom intent recognition service."""

    mcp_name: str = "mcp-telekom"


class MCPTelekom(MCPService[MCPTelekomConfig]):
    """Top-level intent classifier for Telekom voice flows."""

    NAME = "mcp-telekom"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def __init__(self, config: MCPTelekomConfig) -> None:
        super().__init__(config)

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register the Telekom intent tools onto FastMCP via the legacy registry adapter."""
        registry = ToolRegistry(mcp)
        register(registry)


SERVICE_CLASS = MCPTelekom
