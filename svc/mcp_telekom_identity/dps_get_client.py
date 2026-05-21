"""HTTP client for the Slovak Telekom DPS API (party-management + customer-management).

GET-only. Mutating endpoints will live in a separate module when needed.
"""

from __future__ import annotations


class DPSError(Exception):
    """Base class for all DPS client errors."""


class DPSAuthError(DPSError):
    """HTTP 401/403 from DPS — bearer token rejected or insufficient scope."""


class DPSUpstreamError(DPSError):
    """Non-auth 4xx or any 5xx from DPS."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"DPS upstream returned HTTP {status_code}")
        self.status_code = status_code


class DPSTimeoutError(DPSError):
    """Request to DPS exceeded the configured timeout."""


class DPSNetworkError(DPSError):
    """Network-level failure connecting to DPS (DNS, connection refused, etc.)."""


class DPSInvalidResponseError(DPSError):
    """DPS returned 2xx but the body could not be parsed as expected JSON."""


class DPSGetClient:
    """Async GET-only HTTP client for DPS. To be fleshed out in the next tasks."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float,
        *,
        verify_tls: bool,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = timeout_seconds
        self._verify_tls = verify_tls
