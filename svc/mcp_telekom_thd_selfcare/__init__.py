"""Telekom THD Selfcare MCP service — RAG facade over the `indexer` service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pydantic

from lib.mcp_service import MCPService, MCPServiceConfig

from .indexer_client import IndexerClient, LabelsCache
from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


class MCPTelekomThdSelfcareConfig(MCPServiceConfig):
    """Configuration for the Telekom THD Selfcare RAG service."""

    mcp_name: str = "mcp-telekom-thd-selfcare"

    indexer_url: str
    indexer_index_ids: list[int]
    indexer_organization_id: str
    indexer_project_id: str
    indexer_timeout_seconds: float = 30.0
    indexer_labels_cache_ttl_seconds: int = 300

    @pydantic.field_validator("indexer_index_ids", mode="before")
    @classmethod
    def _parse_index_ids(cls, v: Any) -> Any:
        """Accept comma-separated string (e.g. "101,102") in addition to JSON list."""
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v


class MCPTelekomThdSelfcare(MCPService[MCPTelekomThdSelfcareConfig]):
    """RAG facade over one or more indexer knowledge bases (Milvus/BYO)."""

    NAME = "mcp-telekom-thd-selfcare"
    TEAM = "telekom"

    CPU_REQUEST = "100m"
    MEMORY_REQUEST = "256Mi"
    CPU_LIMIT = "1000m"
    MEMORY_LIMIT = "512Mi"

    def setup_tools(self, mcp: FastMCP) -> None:
        """Wire up the indexer client + cache and register the 4 RAG tools."""
        # MCPService.config is typed as the base MCPServiceConfig; narrow to our subclass.
        config = cast("MCPTelekomThdSelfcareConfig", self.config)
        client = IndexerClient(
            base_url=config.indexer_url,
            timeout_seconds=config.indexer_timeout_seconds,
        )
        labels_cache = LabelsCache(ttl_seconds=config.indexer_labels_cache_ttl_seconds)
        register(
            mcp=mcp,
            client=client,
            labels_cache=labels_cache,
            index_ids=config.indexer_index_ids,
            organization_id=config.indexer_organization_id,
            project_id=config.indexer_project_id,
            logger=self.logger,
        )


SERVICE_CLASS = MCPTelekomThdSelfcare
