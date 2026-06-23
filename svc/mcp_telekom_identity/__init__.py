"""Telekom Identity MCP service — DPS-backed customer identification."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

import pydantic
from pydantic_settings import NoDecode

from lib.mcp_service import MCPService, MCPServiceConfig
from lib.mcp_service.legacy_compat import ToolRegistry

from .dps_get_client import DPSGetClient
from .indexer_client import IndexerClient, LabelsCache
from .knowledge_base_tools import register_knowledge_base_tools
from .tools import register

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


_log = logging.getLogger(__name__)

# Identification tools defined but kept out of /mcp (hidden from the LLM/clients).
# Their logic stays intact and tested; flip this set to change visibility.
_HIDDEN_IDENTIFICATION_TOOLS = frozenset({"identifikacia_op", "identifikacia_pas"})


class MCPTelekomIdentityConfig(MCPServiceConfig):
    """Configuration for the Telekom Identity MCP service."""

    mcp_name: str = "mcp-telekom-identity"

    dps_base_url: str = "https://teai.st.sk:8243/omni/test1"
    dps_bearer_token: str = pydantic.Field(default="", exclude=True)
    dps_timeout_seconds: float = 10.0
    dps_verify_tls: bool = False
    dps_max_candidates: int = 10

    goodbot_url: str = "http://goodbot.internal-test.svc.cluster.local:8121"

    # --- Knowledge-base (RAG) facade over the `indexer` service (optional) ---
    # When indexer_url + index_ids + organization_id + project_id are all set, the
    # service additionally exposes the znalostna_baza_* tools. Leave them unset to
    # run identity-only (the KB tools are simply not registered).
    indexer_url: str | None = None
    # NoDecode disables pydantic-settings' default JSON decoding of the env value so a plain
    # comma-separated string (e.g. "442,457,392") reaches the validator below instead of failing
    # the JSON parse in EnvSettingsSource.
    indexer_index_ids: Annotated[list[int], NoDecode] = []  # noqa: RUF012
    indexer_organization_id: str | None = None
    indexer_project_id: str | None = None
    indexer_timeout_seconds: float = 30.0
    indexer_labels_cache_ttl_seconds: int = 300

    @pydantic.field_validator("indexer_index_ids", mode="before")
    @classmethod
    def _parse_index_ids(cls, v: Any) -> Any:
        """Accept comma-separated string (e.g. "442,457,392") in addition to a JSON/list value."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        return v

    @property
    def knowledge_base_enabled(self) -> bool:
        """True when every required indexer setting is present."""
        return bool(
            self.indexer_url
            and self.indexer_index_ids
            and self.indexer_organization_id
            and self.indexer_project_id
        )


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
        """Register identification tools (always) and knowledge-base tools (if configured)."""
        registry = ToolRegistry(mcp)
        register(
            registry,
            client=self._dps_client,
            max_candidates=self._identity_config.dps_max_candidates,
            hidden_tools=_HIDDEN_IDENTIFICATION_TOOLS,
        )
        self._setup_knowledge_base_tools(mcp)

    def _setup_knowledge_base_tools(self, mcp: FastMCP) -> None:
        """Wire up the indexer client + cache and register the znalostna_baza_* tools.

        No-op when the indexer config is incomplete, so identity-only deployments
        (without APP_INDEXER_*) keep starting unchanged.
        """
        config = self._identity_config
        if not config.knowledge_base_enabled:
            _log.info(
                "Knowledge-base tools disabled — set APP_INDEXER_URL, APP_INDEXER_INDEX_IDS, "
                "APP_INDEXER_ORGANIZATION_ID and APP_INDEXER_PROJECT_ID to enable them.",
            )
            return

        client = IndexerClient(
            base_url=config.indexer_url,  # type: ignore[arg-type] — guarded by knowledge_base_enabled
            timeout_seconds=config.indexer_timeout_seconds,
        )
        labels_cache = LabelsCache(ttl_seconds=config.indexer_labels_cache_ttl_seconds)
        register_knowledge_base_tools(
            mcp=mcp,
            client=client,
            labels_cache=labels_cache,
            index_ids=config.indexer_index_ids,
            organization_id=config.indexer_organization_id,  # type: ignore[arg-type]
            project_id=config.indexer_project_id,  # type: ignore[arg-type]
            logger=_log,
        )
        _log.info(
            "Knowledge-base tools enabled (indexes=%s, org=%s, project=%s).",
            config.indexer_index_ids,
            config.indexer_organization_id,
            config.indexer_project_id,
        )


SERVICE_CLASS = MCPTelekomIdentity
