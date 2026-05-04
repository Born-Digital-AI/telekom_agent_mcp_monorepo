"""Endpoints for Kubernetes liveness/readiness Prometheus metrics.

- Doesn't provide any logging/access log
- Doesn't provide the trace id (called by infrastructure components)
"""

from __future__ import annotations

import logging
import socketserver
import threading
import wsgiref.simple_server
import wsgiref.types
from typing import TYPE_CHECKING, Any

from lib.boilerplate import LogColor, ManagedResource, ResourceConfig

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from lib.boilerplate import Metrics

    HTTPHeaders = list[tuple[str, str]]


class HealthzConfig(ResourceConfig):
    """Configuration of the Healthz web server."""

    healthz_protocol: str = "http://"
    healthz_listen_host: str = "0.0.0.0"  # noqa: S104
    healthz_host: str = "127.0.0.1"
    healthz_port: int = 8080  # Different port than the regular HTTP server

    @property
    def healthz_base_url(self) -> str:
        """Base URL (route with root path) of the Healthz web service."""
        return f"{self.healthz_protocol}{self.healthz_host}:{self.healthz_port}"


class SilentHandler(wsgiref.simple_server.WSGIRequestHandler):
    """WSGI handler which doesn't log the requests."""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: ANN401, A002
        """Do nothing."""


class ThreadedWSGIServer(socketserver.ThreadingMixIn, wsgiref.simple_server.WSGIServer):
    """WSGI server which spawns a new thread for each handled request."""

    daemon_threads = True


class HealthzServer(ManagedResource[HealthzConfig]):
    """Synchronous web server running in a background thread.

    Provides endpoints for Kubernetes liveness/readiness probes and Prometheus metrics.
    """

    def __init__(self, config: HealthzConfig, metrics: Metrics | None = None) -> None:
        super().__init__(config, metrics)

        self.wsgi_server: ThreadedWSGIServer
        self.thread: threading.Thread
        self.logger = logging.getLogger()
        self.color = LogColor(self.config.colorize_logs)

        # The following attributes have to be set from the outside before start()
        self.is_ready: Callable[[], bool]  # Attribute wouldn't respect updates from service

        self.routes = {
            ("GET", "/healthz/liveness"): self.handle_liveness,
            ("GET", "/healthz/readiness"): self.handle_readiness,
            ("GET", "/healthz/metrics"): self.handle_metrics,
        }
        # WSGI server adds a content length to the list of headers, so we can't reuse the whole list
        self.default_content_type = ("Content-type", "application/json; charset=utf-8")

    def handle_liveness(self) -> tuple[str, bytes, HTTPHeaders]:
        """Handle the liveness probe."""
        return "200 OK", b'{"status":"up"}', [self.default_content_type]

    def handle_readiness(self) -> tuple[str, bytes, HTTPHeaders]:
        """Handle the readiness probe."""
        ready = self.is_ready()
        status = "200 OK" if ready else "503 Service Unavailable"
        body = b'{"status":"ready"}' if ready else b'{"status":"not ready"}'

        return status, body, [self.default_content_type]

    def handle_metrics(self) -> tuple[str, bytes, HTTPHeaders]:
        """Handle the Prometheus metrics."""
        return "200 OK", self.metrics.collect(), [("Content-type", self.metrics.CONTENT_TYPE)]

    def handle_not_found(self) -> tuple[str, bytes, HTTPHeaders]:
        """Handle an unknown method/path."""
        return ("404 Not Found", b'{"status":"not found"}', [self.default_content_type])

    def handle_exception(self) -> tuple[str, bytes, HTTPHeaders]:
        """Handle an exception."""
        return (
            "500 Internal Server Error",
            b'{status: "unhandled exception"}',
            [self.default_content_type],
        )

    def wsgi_app(
        self,
        environ: dict[str, Any],
        start_response: wsgiref.types.StartResponse,
    ) -> Iterable[bytes]:
        """WSGI application serving the Kubernetes liveness/readiness probes and Prometheus metrics."""
        http_method = environ["REQUEST_METHOD"]
        path = environ["PATH_INFO"]

        try:
            handler = self.routes.get((http_method, path), self.handle_not_found)
            status, body, headers = handler()
        except Exception:
            status, body, headers = self.handle_exception()
            self.logger.exception(
                f"{self.color.red}Healthz server raised an unhandled exception when handling {http_method} {path!r}:{self.color.default}"
            )

        start_response(status, headers)

        return [body]

    async def start(self) -> None:
        """Start the WSGI server in a background thread."""
        self.wsgi_server = ThreadedWSGIServer(
            (self.config.healthz_listen_host, self.config.healthz_port),
            SilentHandler,
            bind_and_activate=True,
        )
        self.wsgi_server.set_app(self.wsgi_app)
        self.wsgi_server.timeout = 0.1  # Max time to block on empty socket [s]
        self.thread = threading.Thread(
            name=self.__class__.__name__, target=self.wsgi_server.serve_forever, daemon=True
        )
        self.thread.start()

    async def stop(self) -> None:
        """Stop the WSGI server in a background thread."""
        self.wsgi_server.shutdown()  # Break the infinite loop for handling of requests
        self.wsgi_server.server_close()  # Stop listening on a socket
        self.thread.join(timeout=0.2)  # Longer than empty socket timeout, delays service shutdown
