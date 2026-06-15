"""Telekom THD Selfcare MCP tools — RAG facade over a single indexer knowledge base.

Tools:
  - list_documents: paginated catalog of documents in the index (with labels, annotations).
  - search:        semantic / hybrid search; returns documents grouped with snippet text.
  - get_document:  full document metadata + chunks (drill-down after search).
  - list_labels:   distinct labels in the index (cached) for discovery / filter hints.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

import pydantic
from mcp.types import ToolAnnotations

from .indexer_client import IndexerClient, IndexerError, LabelsCache

if TYPE_CHECKING:
    import logging

    from mcp.server.fastmcp import FastMCP


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _error(message: str, exc: IndexerError | None = None) -> str:
    payload: dict[str, Any] = {"error": message}
    if exc is not None:
        if exc.status_code is not None:
            payload["status_code"] = exc.status_code
        if exc.body:
            payload["indexer_response"] = exc.body[:500]
    return _json(payload)


def _doc_summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Pick the fields useful to an LLM browsing the catalog. Drop noise like created_by."""
    return {
        "id": doc.get("id"),
        "name": doc.get("name"),
        "annotation": doc.get("annotation"),
        "labels": doc.get("labels") or [],
        "url": doc.get("url"),
        "tokens_count": doc.get("tokens_count"),
        "chunks_count": doc.get("chunks_count"),
        "created_at": doc.get("created_at"),
    }


def register(
    *,
    mcp: FastMCP,
    client: IndexerClient,
    labels_cache: LabelsCache,
    index_ids: list[int],
    organization_id: str,
    project_id: str,
    logger: logging.Logger,
) -> None:
    """Register the 4 RAG tools on the given FastMCP instance.

    The indexer connection parameters are captured by closure — the LLM never sees them.
    When multiple index_ids are provided, list_documents and search fan out to all of them
    in parallel and merge the results. All indexes share the same organization and project.
    """
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True)

    _index_ids_hint = (
        f"Available index IDs: {index_ids}. "
        "When omitted, all indexes are queried and results are merged."
    )

    @mcp.tool(annotations=read_only)
    async def list_documents(
        page: Annotated[int, pydantic.Field(ge=1, description="Page number (1-based)")] = 1,
        limit: Annotated[
            int, pydantic.Field(ge=1, le=100, description="Items per page (max 100)")
        ] = 20,
        search: Annotated[
            str | None, pydantic.Field(description="Optional substring filter on document name")
        ] = None,
        labels: Annotated[
            list[str] | None,
            pydantic.Field(
                description="Optional list of labels — documents must carry all of them"
            ),
        ] = None,
        sort_column: Annotated[
            Literal["id", "name", "created_at", "updated_at", "tokens_count"],
            pydantic.Field(description="Column to sort by"),
        ] = "created_at",
        sort_direction: Annotated[
            Literal["asc", "desc"], pydantic.Field(description="Sort direction")
        ] = "desc",
        index_id: Annotated[
            int | None,
            pydantic.Field(description=f"Restrict to a specific index. {_index_ids_hint}"),
        ] = None,
    ) -> str:
        """List documents in the knowledge base with their labels and annotations."""
        target_ids = [index_id] if index_id is not None else index_ids
        logger.info(
            "list_documents page=%s limit=%s search=%r labels=%s index_ids=%s",
            page,
            limit,
            search,
            labels,
            target_ids,
        )
        raw_results = await asyncio.gather(
            *[
                client.list_documents(
                    index_id=iid,
                    page=page,
                    limit=limit,
                    search=search,
                    labels=labels,
                    sort_column=sort_column,
                    sort_direction=sort_direction,
                )
                for iid in target_ids
            ],
            return_exceptions=True,
        )

        all_docs: list[dict[str, Any]] = []
        total = 0
        has_next = False
        errors: list[dict[str, Any]] = []
        for iid, result in zip(target_ids, raw_results):
            if isinstance(result, IndexerError):
                logger.warning("list_documents indexer error (index_id=%s): %s", iid, result)
                errors.append({"index_id": iid, "error": str(result)})
            elif isinstance(result, Exception):
                logger.warning("list_documents unexpected error (index_id=%s): %s", iid, result)
                errors.append({"index_id": iid, "error": str(result)})
            else:
                all_docs.extend(_doc_summary(d) for d in result.get("data", []))
                total += result.get("total", 0)
                if result.get("has_next"):
                    has_next = True

        payload: dict[str, Any] = {
            "page": page,
            "limit": limit,
            "total": total,
            "has_next": has_next,
            "documents": all_docs,
        }
        if errors:
            payload["errors"] = errors
        return _json(payload)

    @mcp.tool(annotations=read_only)
    async def search(
        query: Annotated[
            str, pydantic.Field(min_length=1, description="Natural-language question or keywords")
        ],
        top_k: Annotated[
            int,
            pydantic.Field(
                ge=1,
                le=100,
                description="Number of result snippets to return per index.",
            ),
        ] = 5,
        labels: Annotated[
            list[str] | None,
            pydantic.Field(
                description=(
                    "Optional labels filter — restrict search to documents carrying all listed "
                    "labels. Use list_labels() to discover available labels."
                )
            ),
        ] = None,
        retrieval_mode: Annotated[
            Literal["vector", "hybrid_bm25"],
            pydantic.Field(
                description=(
                    "`vector` = dense semantic only (default). `hybrid_bm25` = blend dense + BM25 "
                    "full-text; better for keyword-heavy queries (Milvus-only)."
                )
            ),
        ] = "vector",
        bm25_weight: Annotated[
            float,
            pydantic.Field(
                ge=0.0,
                le=1.0,
                description=(
                    "Weight of BM25 in the hybrid blend (0.0 = dense only, 1.0 = BM25 only). "
                    "Ignored when retrieval_mode='vector'."
                ),
            ),
        ] = 0.4,
        amount_adjacent_snippets: Annotated[
            int,
            pydantic.Field(
                ge=0,
                le=5,
                description=(
                    "If > 0, also fetch N neighboring chunks for each hit (more context, more "
                    "tokens). Milvus/BYO only."
                ),
            ),
        ] = 0,
    ) -> str:
        """Search the knowledge base. Fans out to all indexes in parallel and merges results.

        Returns documents with snippet text (no per-chunk scores). When multiple indexes are
        configured, top_k applies per index so the total document count may exceed top_k.
        """
        logger.info(
            "search query=%r top_k=%s labels=%s mode=%s adj=%s index_ids=%s",
            query,
            top_k,
            labels,
            retrieval_mode,
            amount_adjacent_snippets,
            index_ids,
        )
        raw_results = await asyncio.gather(
            *[
                client.search(
                    query=query,
                    index_id=iid,
                    organization_id=organization_id,
                    project_id=project_id,
                    top_k=top_k,
                    labels=labels,
                    retrieval_mode=retrieval_mode,
                    bm25_weight=bm25_weight,
                    amount_adjacent_snippets=amount_adjacent_snippets,
                )
                for iid in index_ids
            ],
            return_exceptions=True,
        )

        seen_doc_ids: set[int] = set()
        all_docs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for iid, result in zip(index_ids, raw_results):
            if isinstance(result, Exception):
                logger.warning("search indexer error (index_id=%s): %s", iid, result)
                errors.append({"index_id": iid, "error": str(result)})
                continue
            for d in result.get("documents", []):
                doc_id = d.get("id")
                if doc_id in seen_doc_ids:
                    continue
                seen_doc_ids.add(doc_id)
                all_docs.append(
                    {
                        "id": doc_id,
                        "name": d.get("name"),
                        "url": d.get("url"),
                        "annotation": d.get("annotation"),
                        "labels": d.get("labels") or [],
                        "chunks": d.get("chunks") or [],
                    }
                )

        payload: dict[str, Any] = {"documents": all_docs}
        if errors:
            payload["errors"] = errors
        return _json(payload)

    @mcp.tool(annotations=read_only)
    async def get_document(
        document_id: Annotated[
            int, pydantic.Field(ge=1, description="Document ID from search or list_documents")
        ],
        include_chunks: Annotated[  # noqa: FBT002 — MCP tools are keyword-called by the LLM
            bool,
            pydantic.Field(
                description="If true, also fetch full chunk list for the document (BYO/Milvus only).",
            ),
        ] = True,
    ) -> str:
        """Return full metadata and (optionally) the chunks of a single document."""
        logger.info("get_document document_id=%s include_chunks=%s", document_id, include_chunks)
        try:
            if include_chunks:
                doc, chunks = await asyncio.gather(
                    client.get_document(document_id),
                    client.list_chunks(document_id),
                )
            else:
                doc = await client.get_document(document_id)
                chunks = None
        except IndexerError as exc:
            logger.warning("get_document indexer error: %s", exc)
            return _error("indexer_request_failed", exc)

        payload: dict[str, Any] = {
            "id": doc.get("id"),
            "name": doc.get("name"),
            "annotation": doc.get("annotation"),
            "labels": doc.get("labels") or [],
            "url": doc.get("url"),
            "tokens_count": doc.get("tokens_count"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
        }
        if chunks is not None:
            payload["chunks"] = [
                {
                    "id": c.get("id"),
                    "title": c.get("title"),
                    "text": c.get("text"),
                    "annotation": c.get("annotation"),
                }
                for c in chunks.get("chunks", [])
            ]
        return _json(payload)

    @mcp.tool(annotations=read_only)
    async def list_labels(
        refresh: Annotated[  # noqa: FBT002 — MCP tools are keyword-called by the LLM
            bool,
            pydantic.Field(description="Bypass cache and recompute from the indexer."),
        ] = False,
    ) -> str:
        """List the distinct labels present in the knowledge base (useful for filter discovery)."""
        logger.info("list_labels refresh=%s", refresh)
        if refresh:
            await labels_cache.invalidate()

        async def _compute() -> dict[str, Any]:
            labels_seen: set[str] = set()
            document_count = 0
            page_size = 100
            for iid in index_ids:
                page = 1
                while True:
                    result = await client.list_documents(
                        index_id=iid, page=page, limit=page_size
                    )
                    docs = result.get("data", [])
                    document_count += len(docs)
                    for d in docs:
                        for label in d.get("labels") or []:
                            labels_seen.add(label)
                    if not result.get("has_next"):
                        break
                    page += 1
            return {
                "labels": sorted(labels_seen),
                "document_count": document_count,
                "cached_at": dt.datetime.now(dt.UTC).isoformat(),
            }

        try:
            payload = await labels_cache.get_or_compute(_compute)
        except IndexerError as exc:
            logger.warning("list_labels indexer error: %s", exc)
            return _error("indexer_request_failed", exc)
        return _json(payload)
