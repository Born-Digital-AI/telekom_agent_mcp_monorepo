"""Telekom Identity MCP service — DPS-backed customer identification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pydantic

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .dps_get_client import DPSGetClient
from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


_log = logging.getLogger(__name__)


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

    def __init__(self, config: MCPTelekomIdentityConfig) -> None:
        super().__init__(config)
        self._identity_config = config
        if not config.dps_bearer_token:
            _log.warning(
                "APP_DPS_BEARER_TOKEN is empty — DPS calls will fail with auth_failed.",
            )
        self._dps_client = DPSGetClient(
            base_url=config.dps_base_url,
            bearer_token=config.dps_bearer_token,
            timeout_seconds=config.dps_timeout_seconds,
            verify_tls=config.dps_verify_tls,
        )

    def setup_tools(self, mcp: FastMCP) -> None:
        """Register identification tools."""
        registry = ToolRegistry(mcp)
        register(
            registry,
            client=self._dps_client,
            max_candidates=self._identity_config.dps_max_candidates,
        )


SERVICE_CLASS = MCPTelekomIdentity
