"""Async HTTP client for the `indexer` service + a tiny TTL cache for labels.

Only the read-only endpoints needed by the RAG facade tools are wrapped:
list documents, search, get document, list chunks. Returns parsed JSON dicts
verbatim — no Pydantic models, the indexer schema is stable enough and the
tools serialize back to JSON anyway.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class IndexerError(RuntimeError):
    """Indexer returned a non-2xx response or was unreachable."""

    def __init__(
        self, message: str, *, status_code: int | None = None, body: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class IndexerClient:
    """Thin async wrapper around the indexer HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,  # noqa: ANN401 — accepts arbitrary JSON-serializable body
    ) -> Any:  # noqa: ANN401 — returns parsed JSON; downstream callers narrow it
        try:
            response = await self._client.request(method, path, params=params, json=json)
        except httpx.HTTPError as exc:
            msg = f"indexer request failed ({method} {path}): {exc}"
            raise IndexerError(msg) from exc

        if response.is_error:
            msg = f"indexer returned HTTP {response.status_code} for {method} {path}"
            raise IndexerError(msg, status_code=response.status_code, body=response.text)
        return response.json()

    async def list_documents(  # noqa: PLR0913 — mirrors indexer query parameters
        self,
        index_id: int,
        *,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        labels: list[str] | None = None,
        sort_column: str = "created_at",
        sort_direction: str = "desc",
    ) -> dict[str, Any]:
        """GET /documents/list/{index_id}. `labels` filter rides in the request body per swagger."""
        params: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "sort_column": sort_column,
            "sort_direction": sort_direction,
        }
        if search:
            params["search"] = search
        return await self._request(
            "GET",
            f"/documents/list/{index_id}",
            params=params,
            json=labels or None,
        )

    async def search(  # noqa: PLR0913 — mirrors indexer SearchRequest fields
        self,
        *,
        query: str,
        index_id: int,
        organization_id: str,
        project_id: str,
        top_k: int = 5,
        labels: list[str] | None = None,
        retrieval_mode: str = "vector",
        bm25_weight: float = 0.4,
        amount_adjacent_snippets: int = 0,
    ) -> dict[str, Any]:
        """POST /search/index."""
        body: dict[str, Any] = {
            "query": query,
            "index_id": index_id,
            "organization_id": organization_id,
            "project_id": project_id,
            "top_k": top_k,
            "retrieval_mode": retrieval_mode,
            "bm25_weight": bm25_weight,
            "amount_adjacent_snippets": amount_adjacent_snippets,
        }
        if labels:
            body["labels"] = labels
        return await self._request("POST", "/search/index", json=body)

    async def get_document(self, document_id: int) -> dict[str, Any]:
        """GET /documents/{document_id}."""
        return await self._request("GET", f"/documents/{document_id}")

    async def list_chunks(self, document_id: int) -> dict[str, Any]:
        """GET /chunks/list/{document_id} (BYO/Milvus only)."""
        return await self._request("GET", f"/chunks/list/{document_id}")


class LabelsCache:
    """Process-local TTL cache for the distinct-labels list (one entry per service)."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._value: dict[str, Any] | None = None
        self._expires_at: float = 0.0

    async def get_or_compute(
        self, compute: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        """Return the cached value or recompute via the awaitable factory `compute`."""
        async with self._lock:
            if self._value is not None and time.monotonic() < self._expires_at:
                return self._value
            value = await compute()
            self._value = value
            self._expires_at = time.monotonic() + self._ttl
            return value

    async def invalidate(self) -> None:
        """Drop the cached value so the next `get_or_compute` recomputes."""
        async with self._lock:
            self._value = None
            self._expires_at = 0.0
