"""HTTP client for the Slovak Telekom DPS API (party-management + customer-management).

GET-only. Mutating endpoints will live in a separate module when needed.

The underlying ``httpx.AsyncClient`` is created lazily on first call. The class
also supports ``async with`` for tests where eager open/close is convenient,
but the service does not have to use the context manager — the same instance
can be created in ``__init__`` and reused across all tool invocations.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

import httpx

from lib.boilerplate.logging import current_conversation_id, current_interaction_id

if TYPE_CHECKING:
    from types import TracebackType
    from typing import Self


_log = logging.getLogger(__name__)

_HTTP_CLIENT_ERROR_THRESHOLD = 400
_HTTP_NOT_FOUND = 404


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
    """Async GET-only HTTP client for DPS."""

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
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout_seconds,
                verify=self._verify_tls,
            )
        return self._client

    async def __aenter__(self) -> Self:
        self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        conv = current_conversation_id.get("") or uuid.uuid4().hex
        inter = current_interaction_id.get("") or uuid.uuid4().hex
        return {
            "authorization": f"Bearer {self._bearer_token}",
            "accept": "application/json",
            "x-request-id": uuid.uuid4().hex,
            "x-request-session-id": conv,
            "x-request-tracking-id": inter,
        }

    async def get_parties_by_identification(
        self,
        identification_id: str,
        identification_type: str,
    ) -> list[dict[str, Any]]:
        """GET /party-management/3.54.0/v2/parties — resolve identification → party records."""
        result = await self._get(
            "/party-management/3.54.0/v2/parties",
            {
                "identificationId": identification_id,
                "identificationType": identification_type,
                "fields": "*",
            },
        )
        if not isinstance(result, list):
            msg = "party-management expected JSON array"
            raise DPSInvalidResponseError(msg)
        return result

    async def get_customers_by_engaged_party(
        self,
        party_id: str,
    ) -> list[dict[str, Any]]:
        """GET /customer-management/4.67.0/customers — resolve PARTY_id → customer records."""
        result = await self._get(
            "/customer-management/4.67.0/customers",
            {
                "engagedParty.id": party_id,
                "fields": "*",
            },
        )
        if not isinstance(result, list):
            msg = "customer-management expected JSON array"
            raise DPSInvalidResponseError(msg)
        return result

    async def get_customer_by_id(self, customer_id: str) -> dict[str, Any] | None:
        """GET /customer-management/4.67.0/customers/{id} — single Customer or None on 404."""
        try:
            result = await self._get(
                f"/customer-management/4.67.0/customers/{customer_id}", {"fields": "*"}
            )
        except DPSUpstreamError as exc:
            if exc.status_code == _HTTP_NOT_FOUND:
                return None
            raise
        if not isinstance(result, dict):
            msg = "customer-management single fetch expected JSON object"
            raise DPSInvalidResponseError(msg)
        return result

    async def get_billing_account_by_id(self, account_id: str) -> dict[str, Any] | None:
        """GET /customer-management/4.67.0/billingAccounts/{id} — single BillingAccount or None on 404."""
        try:
            result = await self._get(
                f"/customer-management/4.67.0/billingAccounts/{account_id}",
                {"fields": "*"},
            )
        except DPSUpstreamError as exc:
            if exc.status_code == _HTTP_NOT_FOUND:
                return None
            raise
        if not isinstance(result, dict):
            msg = "billingAccount single fetch expected JSON object"
            raise DPSInvalidResponseError(msg)
        return result

    async def get_products_by_public_identifier(
        self,
        public_identifier: str,
    ) -> list[dict[str, Any]]:
        """GET /product-inventory/4.64/products?query=publicIdentifier==<msisdn> — list of products."""
        result = await self._get(
            "/product-inventory/4.64/products",
            {
                "query": f"publicIdentifier=={public_identifier}",
                "fields": "*",
                "size": "20",
            },
        )
        if not isinstance(result, list):
            msg = "product-inventory expected JSON array"
            raise DPSInvalidResponseError(msg)
        return result

    async def get_products_by_serial_number(
        self,
        serial_number: str,
    ) -> list[dict[str, Any]]:
        """GET /product-inventory/4.64/products?query=productSerialNumber==<sn> — list of products."""
        result = await self._get(
            "/product-inventory/4.64/products",
            {
                "query": f"productSerialNumber=={serial_number}",
                "fields": "*",
                "size": "20",
            },
        )
        if not isinstance(result, list):
            msg = "product-inventory expected JSON array"
            raise DPSInvalidResponseError(msg)
        return result

    async def _get(self, path: str, params: dict[str, str]) -> Any:  # noqa: ANN401
        client = self._ensure_client()
        url = self._base_url + path
        _log.info("DPS GET %s", path)
        try:
            response = await client.get(url, params=params, headers=self._headers())
        except httpx.TimeoutException as exc:
            _log.warning("DPS GET %s timed out", path)
            raise DPSTimeoutError(str(exc)) from exc
        except httpx.RequestError as exc:
            _log.warning("DPS GET %s network error: %s", path, exc)
            raise DPSNetworkError(str(exc)) from exc

        status = response.status_code
        if status in (401, 403):
            _log.warning("DPS GET %s -> HTTP %s (auth)", path, status)
            msg = f"DPS rejected the bearer token (HTTP {status})"
            raise DPSAuthError(msg)
        if status >= _HTTP_CLIENT_ERROR_THRESHOLD:
            _log.warning("DPS GET %s -> HTTP %s", path, status)
            raise DPSUpstreamError(status)

        try:
            return response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            _log.warning("DPS GET %s returned invalid JSON: %s", path, exc)
            raise DPSInvalidResponseError(str(exc)) from exc
