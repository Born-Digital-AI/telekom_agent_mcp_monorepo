"""Unit tests for the identity service's knowledge-base (RAG) tools.

The tools talk to a fake `IndexerClient` injected through
`register_knowledge_base_tools()`; no real HTTP traffic. Covers: happy path per
tool, error handling (errors surfaced per-index), and labels-cache TTL.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from svc.mcp_telekom_identity.indexer_client import IndexerError, LabelsCache
from svc.mcp_telekom_identity.knowledge_base_tools import register_knowledge_base_tools

SEARCH_TOOL = "znalostna_baza_vyhladaj"
LIST_TOOL = "znalostna_baza_zoznam_dokumentov"
GET_TOOL = "znalostna_baza_detail_dokumentu"
LABELS_TOOL = "znalostna_baza_stitky"


class _FakeMCP:
    """Captures tools registered via `@mcp.tool(name=..., annotations=...)`."""

    def __init__(self) -> None:
        self.registered: dict[str, Any] = {}

    def tool(
        self,
        *,
        name: str | None = None,
        annotations: Any = None,  # noqa: ARG002
        description: str | None = None,  # noqa: ARG002
    ):
        def decorator(fn: Any) -> Any:
            self.registered[name or fn.__name__] = fn
            return fn

        return decorator


class _FakeClient:
    """Async-method stub that records calls and returns scripted responses."""

    def __init__(self) -> None:
        self.list_documents_responses: list[dict[str, Any] | Exception] = []
        self.search_responses: list[dict[str, Any] | Exception] = []
        self.get_document_responses: list[dict[str, Any] | Exception] = []
        self.list_chunks_responses: list[dict[str, Any] | Exception] = []
        # name -> index record (with "id"); used by get_index_by_name.
        self.index_by_name: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def _take(queue: list[Any]) -> Any:
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    async def list_documents(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("list_documents", kwargs))
        return self._take(self.list_documents_responses)

    async def search(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("search", kwargs))
        return self._take(self.search_responses)

    async def get_document(self, document_id: int) -> dict[str, Any]:
        self.calls.append(("get_document", {"document_id": document_id}))
        return self._take(self.get_document_responses)

    async def list_chunks(self, document_id: int) -> dict[str, Any]:
        self.calls.append(("list_chunks", {"document_id": document_id}))
        return self._take(self.list_chunks_responses)

    async def get_index_by_name(
        self, name: str, *, organization_id: str, project_id: str
    ) -> dict[str, Any]:
        self.calls.append(
            ("get_index_by_name", {"name": name, "org": organization_id, "proj": project_id})
        )
        return self.index_by_name[name]


@pytest.fixture
def fixture() -> tuple[dict[str, Any], _FakeClient, LabelsCache]:
    """Wire a fresh fake MCP + fake client + cache for each test."""
    fake_mcp = _FakeMCP()
    client = _FakeClient()
    cache = LabelsCache(ttl_seconds=60)
    register_knowledge_base_tools(
        mcp=fake_mcp,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        labels_cache=cache,
        index_ids=[42],
        organization_id="org-1",
        project_id="proj-1",
        logger=logging.getLogger("test"),
    )
    return fake_mcp.registered, client, cache


@pytest.mark.unit
async def test_register_exposes_exactly_four_tools(fixture) -> None:
    tools, _client, _cache = fixture
    assert set(tools.keys()) == {SEARCH_TOOL, LIST_TOOL, GET_TOOL, LABELS_TOOL}


@pytest.mark.unit
async def test_list_documents_returns_summary_and_pagination(fixture) -> None:
    tools, client, _ = fixture
    client.list_documents_responses.append(
        {
            "page": 1,
            "limit": 20,
            "total": 1,
            "has_next": False,
            "data": [
                {
                    "id": 7,
                    "name": "router-guide.md",
                    "annotation": "How to configure",
                    "labels": ["wifi", "setup"],
                    "url": "https://example.com/g",
                    "tokens_count": 1234,
                    "chunks_count": 5,
                    "created_at": "2026-01-01T00:00:00Z",
                    "created_by": "noisy-field-stripped",
                }
            ],
        }
    )

    result = json.loads(await tools[LIST_TOOL](labels=["wifi"]))

    assert result["total"] == 1
    assert result["has_next"] is False
    assert result["documents"][0]["id"] == 7
    assert result["documents"][0]["labels"] == ["wifi", "setup"]
    assert "created_by" not in result["documents"][0]
    method, kwargs = client.calls[0]
    assert method == "list_documents"
    assert kwargs["index_id"] == 42
    assert kwargs["labels"] == ["wifi"]


@pytest.mark.unit
async def test_search_passes_env_scoped_ids_and_returns_snippets(fixture) -> None:
    tools, client, _ = fixture
    client.search_responses.append(
        {
            "documents": [
                {
                    "id": 1,
                    "name": "doc",
                    "url": None,
                    "annotation": None,
                    "labels": ["t"],
                    "chunks": ["snippet A", "snippet B"],
                }
            ]
        }
    )

    result = json.loads(
        await tools[SEARCH_TOOL](query="how to reset router", top_k=3, retrieval_mode="hybrid_bm25")
    )

    assert result["documents"][0]["chunks"] == ["snippet A", "snippet B"]
    method, kwargs = client.calls[0]
    assert method == "search"
    assert kwargs["index_id"] == 42
    assert kwargs["organization_id"] == "org-1"
    assert kwargs["project_id"] == "proj-1"
    assert kwargs["top_k"] == 3
    assert kwargs["retrieval_mode"] == "hybrid_bm25"


@pytest.mark.unit
async def test_get_document_fetches_doc_and_chunks_in_parallel(fixture) -> None:
    tools, client, _ = fixture
    client.get_document_responses.append(
        {
            "id": 5,
            "name": "x",
            "annotation": "a",
            "labels": ["l"],
            "url": None,
            "tokens_count": 100,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": None,
        }
    )
    client.list_chunks_responses.append(
        {"chunks": [{"id": 9, "title": "T", "text": "body", "annotation": None}]}
    )

    result = json.loads(await tools[GET_TOOL](document_id=5))

    assert result["id"] == 5
    assert result["chunks"] == [{"id": 9, "title": "T", "text": "body", "annotation": None}]
    methods = {c[0] for c in client.calls}
    assert methods == {"get_document", "list_chunks"}


@pytest.mark.unit
async def test_get_document_without_chunks_skips_chunks_call(fixture) -> None:
    tools, client, _ = fixture
    client.get_document_responses.append(
        {
            "id": 5,
            "name": "x",
            "annotation": None,
            "labels": [],
            "url": None,
            "tokens_count": None,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": None,
        }
    )

    result = json.loads(await tools[GET_TOOL](document_id=5, include_chunks=False))

    assert "chunks" not in result
    assert [c[0] for c in client.calls] == ["get_document"]


@pytest.mark.unit
async def test_list_labels_aggregates_across_pages_and_caches(fixture) -> None:
    tools, client, _cache = fixture
    client.list_documents_responses.extend(
        [
            {
                "data": [{"labels": ["a", "b"]}, {"labels": ["b"]}],
                "has_next": True,
            },
            {
                "data": [{"labels": ["c"]}, {"labels": []}],
                "has_next": False,
            },
        ]
    )

    first = json.loads(await tools[LABELS_TOOL]())

    assert first["labels"] == ["a", "b", "c"]
    assert first["document_count"] == 4
    assert len([c for c in client.calls if c[0] == "list_documents"]) == 2

    # Second call should hit cache → no further indexer requests.
    second = json.loads(await tools[LABELS_TOOL]())
    assert second["labels"] == first["labels"]
    assert len([c for c in client.calls if c[0] == "list_documents"]) == 2


@pytest.mark.unit
async def test_list_labels_refresh_busts_cache(fixture) -> None:
    tools, client, _cache = fixture
    client.list_documents_responses.extend(
        [
            {"data": [{"labels": ["a"]}], "has_next": False},
            {"data": [{"labels": ["a", "b"]}], "has_next": False},
        ]
    )

    first = json.loads(await tools[LABELS_TOOL]())
    assert first["labels"] == ["a"]

    second = json.loads(await tools[LABELS_TOOL](refresh=True))
    assert second["labels"] == ["a", "b"]


@pytest.mark.unit
async def test_index_names_are_resolved_to_ids_and_cached() -> None:
    """index_names → resolved to ids via get_index_by_name once, then reused (cached)."""
    fake_mcp = _FakeMCP()
    client = _FakeClient()
    client.index_by_name = {"kb-faq": {"id": 442}, "kb-docs": {"id": 457}}
    register_knowledge_base_tools(
        mcp=fake_mcp,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        labels_cache=LabelsCache(ttl_seconds=60),
        index_names=["kb-faq", "kb-docs"],
        organization_id="org-1",
        project_id="proj-1",
        logger=logging.getLogger("test"),
    )
    tools = fake_mcp.registered

    client.search_responses.extend([{"documents": []}, {"documents": []}])
    await tools[SEARCH_TOOL](query="q")

    # Both names resolved exactly once, then search fanned out to the resolved ids.
    resolved_calls = [c for c in client.calls if c[0] == "get_index_by_name"]
    assert {c[1]["name"] for c in resolved_calls} == {"kb-faq", "kb-docs"}
    search_ids = sorted(c[1]["index_id"] for c in client.calls if c[0] == "search")
    assert search_ids == [442, 457]

    # Second call reuses the cached ids — no further resolution.
    client.search_responses.extend([{"documents": []}, {"documents": []}])
    await tools[SEARCH_TOOL](query="q2")
    assert len([c for c in client.calls if c[0] == "get_index_by_name"]) == 2


@pytest.mark.unit
async def test_search_surfaces_indexer_error_per_index(fixture) -> None:
    tools, client, _ = fixture
    client.search_responses.append(
        IndexerError("boom", status_code=503, body="service unavailable")
    )

    result = json.loads(await tools[SEARCH_TOOL](query="q"))

    # Multi-index fan-out collects per-index failures under "errors" and still
    # returns a (possibly empty) documents list.
    assert result["documents"] == []
    assert result["errors"][0]["index_id"] == 42
    assert "boom" in result["errors"][0]["error"]
