"""MCP-based service boilerplate built on the generic service lifecycle.

- Provides a base class that runs an MCP server (stdio / SSE / streamable HTTP)
- Wires :mod:`lib.mcp_service.middleware` for HTTP transports so that trace,
  conversation, and interaction IDs flow into log records.
"""

from __future__ import annotations

import asyncio
import threading
from typing import cast

import uvicorn
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from pydantic import AnyHttpUrl, BaseModel, TypeAdapter

from lib.boilerplate import Service, ServiceConfig
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
    issuer_url: str = "http://localhost"

    def get_auth_settings(self) -> AuthSettings:
        """Return AuthSettings configured for API-key auth."""
        issuer: AnyHttpUrl = TypeAdapter(AnyHttpUrl).validate_python(self.issuer_url)
        return AuthSettings(
            issuer_url=issuer,
            resource_server_url=None,
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
    mcp_auth_api_key: str | None = None

    mcp_stateless_http: bool = True


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
        auth_settings: AuthSettings | None = None
        token_verifier: TokenVerifier | None = None

        if self.config.mcp_auth_enabled:
            auth = MCPAuth(enabled=True, api_key=self.config.mcp_auth_api_key)
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
