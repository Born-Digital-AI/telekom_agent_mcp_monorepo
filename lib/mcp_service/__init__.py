"""MCP-based service boilerplate built on the generic service lifecycle.

- Provides a base class that runs an MCP server (stdio / SSE / streamable HTTP)
- Wires :mod:`lib.mcp_service.middleware` for HTTP transports so that trace,
  conversation, and interaction IDs flow into log records.
"""

from __future__ import annotations

import asyncio
import threading
from typing import cast

import pydantic
import uvicorn
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter

from lib.boilerplate import Service, ServiceConfig
from lib.boilerplate.exceptions import ConfigError
from lib.healthz import HealthzConfig, HealthzServer
from lib.mcp_service.middleware import wrap_with_tracing


class MCPAuth(BaseModel):
    """Minimal API-key auth wrapper that also implements token verification.

    - Stores configured API keys
    - Exposes `auth_settings` required by FastMCP
    - Implements `verify_token` to work as a TokenVerifier
    """

    enabled: bool = False
    api_key: str | None = None
    issuer_url: str
    resource_server_url: str | None = None

    def get_auth_settings(self) -> AuthSettings:
        """Return AuthSettings configured for API-key auth."""
        issuer: AnyHttpUrl = TypeAdapter(AnyHttpUrl).validate_python(self.issuer_url)
        resource: AnyHttpUrl | None = None
        if self.resource_server_url:
            resource = TypeAdapter(AnyHttpUrl).validate_python(self.resource_server_url)
        return AuthSettings(
            issuer_url=issuer,
            resource_server_url=resource,
            required_scopes=None,
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a bearer token against configured API keys."""
        if self.api_key and token == self.api_key:
            return AccessToken(token=token, client_id="api-key", scopes=[], expires_at=None)
        return None

    def get_token_verifier(self) -> TokenVerifier:
        """Return self as a TokenVerifier for FastMCP, because AuthSettings requires a TokenVerifier with a verify_token method."""
        return cast("TokenVerifier", self)


class MCPServiceConfig(ServiceConfig, HealthzConfig):
    """Configuration of an MCP service."""

    mcp_name: str = "mcp-server"
    mcp_transport: str = "streamable-http"  # "stdio", "sse", "streamable-http"
    mcp_host: str = "0.0.0.0"  # noqa: S104
    mcp_port: int = 8000
    mcp_mount_path: str | None = None  # Used only for SSE

    # Optional Bearer token (API key) auth for HTTP transports
    mcp_auth_enabled: bool = False
    mcp_auth_api_key: str | None = pydantic.Field(default=None, exclude=True)
    mcp_auth_issuer_url: str = "http://localhost"
    mcp_auth_resource_server_url: str | None = None

    # Stateful mode breaks log-context propagation (see MCPService.run_forever).
    # Keep this True unless you know what you're doing.
    mcp_stateless_http: bool = True

    @pydantic.model_validator(mode="after")
    def _validate_ports(self) -> MCPServiceConfig:
        """MCP and healthz must not collide on the same port."""
        if self.mcp_port == self.healthz_port:
            msg = (
                f"mcp_port and healthz_port are both {self.mcp_port}. "
                "They must differ — set APP_MCP_PORT or APP_HEALTHZ_PORT to a different value."
            )
            raise ValueError(msg)
        return self


class MCPService[ConcreteConfig: MCPServiceConfig](Service[MCPServiceConfig]):
    """Service that hosts an MCP server.

    Concrete services should override :meth:`setup_tools` (and optionally
    :meth:`setup_resources` / :meth:`setup_prompts`) to register MCP capabilities.
    """

    def __init__(self, config: ConcreteConfig) -> None:
        super().__init__(config)

        self.healthz = self.register_resource(HealthzServer)
        self.healthz.is_ready = self.is_ready

    def setup_tools(self, mcp: FastMCP) -> None:  # To be overridden by child classes
        """Define MCP tools on the provided `mcp` instance."""

    def setup_resources(self, mcp: FastMCP) -> None:  # To be overridden by child classes
        """Define MCP resources on the provided `mcp` instance."""

    def setup_prompts(self, mcp: FastMCP) -> None:  # To be overridden by child classes
        """Define MCP prompts on the provided `mcp` instance."""

    def register_http_endpoints(self, mcp: FastMCP) -> None:
        """Register optional custom HTTP endpoints when needed."""

    async def run_forever(self) -> None:
        """Start the MCP server and keep it running until shutdown is requested."""
        # Stateful HTTP mode breaks the logging contract: the per-session task is spawned
        # at session-creation time and outlives the request whose ContextVars set
        # conversation_id / interaction_id. Subsequent requests' headers never reach the
        # tool handlers. Refuse to start so the failure is loud and obvious.
        if not self.config.mcp_stateless_http and self.config.mcp_transport != "stdio":
            msg = (
                "mcp_stateless_http=False is not supported: it breaks the "
                "X-Conversation-Id / X-Interaction-Id propagation into log records. "
                "Set APP_MCP_STATELESS_HTTP=true (default) or use stdio transport."
            )
            raise ConfigError(msg)

        auth_settings: AuthSettings | None = None
        token_verifier: TokenVerifier | None = None

        if self.config.mcp_auth_enabled:
            if not self.config.mcp_auth_api_key:
                msg = "mcp_auth_enabled=True requires APP_MCP_AUTH_API_KEY to be set."
                raise ConfigError(msg)
            auth = MCPAuth(
                enabled=True,
                api_key=self.config.mcp_auth_api_key,
                issuer_url=self.config.mcp_auth_issuer_url,
                resource_server_url=self.config.mcp_auth_resource_server_url,
            )
            auth_settings = auth.get_auth_settings()
            token_verifier = auth.get_token_verifier()

        mcp = FastMCP(
            self.config.mcp_name,
            host=self.config.mcp_host,
            port=self.config.mcp_port,
            auth=auth_settings,
            token_verifier=token_verifier,
            stateless_http=self.config.mcp_stateless_http,
        )
        self.setup_tools(mcp)
        self.setup_resources(mcp)
        self.setup_prompts(mcp)
        self.register_http_endpoints(mcp)

        transport = self.config.mcp_transport

        def serve_blocking_stdio() -> None:
            mcp.run("stdio")

        def serve_blocking_http() -> None:
            # Get the underlying ASGI app and wrap it with our tracing middleware so
            # X-Trace-Id / X-Conversation-Id / X-Interaction-Id are bound into ContextVars
            # before any tool runs.
            app = mcp.sse_app() if transport == "sse" else mcp.streamable_http_app()
            app = wrap_with_tracing(app)
            uvicorn.run(
                app,
                host=self.config.mcp_host,
                port=self.config.mcp_port,
                log_config=None,  # Reuse the root logger configured by lib.boilerplate.logging
            )

        target = serve_blocking_stdio if transport == "stdio" else serve_blocking_http
        server_thread = threading.Thread(target=target, name="mcp-server", daemon=True)
        server_thread.start()

        try:
            while not self.shutdown_requested:  # noqa: ASYNC110
                await asyncio.sleep(0.1)
        finally:
            # Daemon thread will exit with the process; nothing to join here
            pass
