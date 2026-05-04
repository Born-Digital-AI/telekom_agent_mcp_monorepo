"""Telekom Main Triage MCP service (selfcare routing + handover)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomMainTriageConfig(MCPServiceConfig):
    """Configuration for the Telekom Main Triage service."""

    mcp_name: str = "mcp-telekom-main-triage"


class MCPTelekomMainTriage(MCPService[MCPTelekomMainTriageConfig]):
    """First-touch triage: pick a selfcare process or hand off to a human."""

    NAME = "mcp-telekom-main-triage"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def __init__(self, config: MCPTelekomMainTriageConfig) -> None:
        super().__init__(config)

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register the Main Triage tools onto FastMCP via the legacy registry adapter."""
        registry = ToolRegistry(mcp)
        register(registry)


SERVICE_CLASS = MCPTelekomMainTriage
