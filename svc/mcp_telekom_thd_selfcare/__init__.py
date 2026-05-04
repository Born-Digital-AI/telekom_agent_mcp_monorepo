"""Telekom THD Selfcare MCP service (fixed internet troubleshooting)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomThdSelfcareConfig(MCPServiceConfig):
    """Configuration for the Telekom THD Selfcare service."""

    mcp_name: str = "mcp-telekom-thd-selfcare"


class MCPTelekomThdSelfcare(MCPService[MCPTelekomThdSelfcareConfig]):
    """Diagnostic and step-by-step troubleshooting for fixed internet customers."""

    NAME = "mcp-telekom-thd-selfcare"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def __init__(self, config: MCPTelekomThdSelfcareConfig) -> None:
        super().__init__(config)

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register the THD Selfcare tools onto FastMCP via the legacy registry adapter."""
        registry = ToolRegistry(mcp)
        register(registry)


SERVICE_CLASS = MCPTelekomThdSelfcare
