"""Telekom Identity MCP service — DPS-backed customer identification."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomIdentityConfig(MCPServiceConfig):
    """Configuration for the Telekom Identity MCP service."""

    mcp_name: str = "mcp-telekom-identity"

    dps_base_url: str = "https://teai.st.sk:8243/omni/test1"
    dps_bearer_token: str = pydantic.Field(default="", exclude=True)
    dps_timeout_seconds: float = 10.0
    dps_verify_tls: bool = False
    dps_max_candidates: int = 10


class MCPTelekomIdentity(MCPService[MCPTelekomIdentityConfig]):
    """Customer identification via DPS party-management + customer-management."""

    NAME = "mcp-telekom-identity"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register identification tools."""
        registry = ToolRegistry(mcp)
        # tools wired in a later task
        _ = registry


SERVICE_CLASS = MCPTelekomIdentity
