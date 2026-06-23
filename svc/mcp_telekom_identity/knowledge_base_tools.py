"""Knowledge-base (RAG) tools for mcp_telekom_identity — a facade over the `indexer` service.

These tools are independent of the DPS identification flow; they let the agent
search and browse the Telekom knowledge base(s) configured via the
``APP_INDEXER_*`` env vars. Registration is optional — when the indexer config
is absent the service starts without them (see ``__init__.py``).

Tools (registered under ``znalostna_baza_*`` to sit cleanly next to the
``identifikacia_*`` tools):
  - znalostna_baza_vyhladaj        → semantic / hybrid search; documents + snippets
  - znalostna_baza_zoznam_dokumentov → paginated catalog (labels, annotations)
  - znalostna_baza_detail_dokumentu  → full document metadata + chunks
  - znalostna_baza_stitky          → distinct labels (cached) for filter discovery

Mirrors ``svc/mcp_telekom_thd_selfcare/tools.py`` (RAG facade) so both services
behave identically against the indexer.
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


def register_knowledge_base_tools(
    *,
    mcp: FastMCP,
    client: IndexerClient,
    labels_cache: LabelsCache,
    index_ids: list[int] | None = None,
    index_names: list[str] | None = None,
    organization_id: str,
    project_id: str,
    logger: logging.Logger,
) -> None:
    """Register the 4 knowledge-base tools on the given FastMCP instance.

    The indexer connection parameters are captured by closure — the LLM never sees them.
    Indexes are addressed either by numeric ``index_ids`` or by human ``index_names``
    (resolved to ids on first use via /indexes/by-name and cached). When several indexes
    are configured, list_documents and search fan out to all of them in parallel and merge
    the results. All indexes share the same organization and project.
    """
    read_only = ToolAnnotations(readOnlyHint=True, idempotentHint=True)

    configured_ids = list(index_ids or [])
    configured_names = list(index_names or [])
    # Lazily resolve index_names → ids the first time a tool runs (setup is sync; the
    # by-name lookup is async). Cached afterwards; the lock prevents duplicate resolution
    # under concurrent first calls.
    _resolved_ids: list[int] = list(configured_ids)
    _resolve_lock = asyncio.Lock()

    async def _get_index_ids() -> list[int]:
        nonlocal _resolved_ids
        if _resolved_ids:
            return _resolved_ids
        async with _resolve_lock:
            if _resolved_ids:
                return _resolved_ids
            resolved: list[int] = []
            for name in configured_names:
                info = await client.get_index_by_name(
                    name, organization_id=organization_id, project_id=project_id
                )
                iid = info.get("id")
                if iid is None:
                    logger.warning("index name %r resolved to a record without an id", name)
                    continue
                resolved.append(int(iid))
            _resolved_ids = resolved
            logger.info("resolved index names %s → ids %s", configured_names, resolved)
            return _resolved_ids

    @mcp.tool(name="znalostna_baza_zoznam_dokumentov", annotations=read_only)
    async def list_documents(
        page: Annotated[int, pydantic.Field(ge=1, description="Číslo strany (od 1).")] = 1,
        limit: Annotated[
            int, pydantic.Field(ge=1, le=100, description="Počet položiek na stranu (max 100).")
        ] = 20,
        search: Annotated[
            str | None, pydantic.Field(description="Voliteľný filter na podreťazec v názve dokumentu.")
        ] = None,
        labels: Annotated[
            list[str] | None,
            pydantic.Field(
                description="Voliteľný zoznam štítkov — dokument musí niesť všetky uvedené."
            ),
        ] = None,
        sort_column: Annotated[
            Literal["id", "name", "created_at", "updated_at", "tokens_count"],
            pydantic.Field(description="Stĺpec na zoradenie."),
        ] = "created_at",
        sort_direction: Annotated[
            Literal["asc", "desc"], pydantic.Field(description="Smer zoradenia.")
        ] = "desc",
        index_id: Annotated[
            int | None,
            pydantic.Field(
                description=(
                    "Obmedz na konkrétny index ID. Ak vynecháš, prehľadajú sa všetky "
                    "nakonfigurované indexy a výsledky sa zlúčia."
                )
            ),
        ] = None,
    ) -> str:
        """Vypíš dokumenty v znalostnej báze aj s ich štítkami a anotáciami."""
        target_ids = [index_id] if index_id is not None else await _get_index_ids()
        logger.info(
            "znalostna_baza_zoznam_dokumentov page=%s limit=%s search=%r labels=%s index_ids=%s",
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
            if isinstance(result, Exception):
                logger.warning(
                    "znalostna_baza_zoznam_dokumentov error (index_id=%s): %s", iid, result
                )
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

    @mcp.tool(name="znalostna_baza_vyhladaj", annotations=read_only)
    async def search(
        query: Annotated[
            str, pydantic.Field(min_length=1, description="Otázka v prirodzenom jazyku alebo kľúčové slová.")
        ],
        top_k: Annotated[
            int,
            pydantic.Field(
                ge=1,
                le=100,
                description="Počet výsledných úryvkov na index.",
            ),
        ] = 5,
        labels: Annotated[
            list[str] | None,
            pydantic.Field(
                description=(
                    "Voliteľný filter štítkov — obmedz hľadanie na dokumenty so všetkými "
                    "uvedenými štítkami. Dostupné štítky zistíš cez znalostna_baza_stitky()."
                )
            ),
        ] = None,
        retrieval_mode: Annotated[
            Literal["vector", "hybrid_bm25"],
            pydantic.Field(
                description=(
                    "`vector` = len husté sémantické (default). `hybrid_bm25` = mix husté + BM25 "
                    "fulltext; lepšie pre dopyty s kľúčovými slovami (len Milvus)."
                )
            ),
        ] = "vector",
        bm25_weight: Annotated[
            float,
            pydantic.Field(
                ge=0.0,
                le=1.0,
                description=(
                    "Váha BM25 v hybridnom mixe (0.0 = len husté, 1.0 = len BM25). "
                    "Ignorované pri retrieval_mode='vector'."
                ),
            ),
        ] = 0.4,
        amount_adjacent_snippets: Annotated[
            int,
            pydantic.Field(
                ge=0,
                le=5,
                description=(
                    "Ak > 0, dotiahni aj N susedných chunkov ku každému zásahu (viac kontextu, "
                    "viac tokenov). Len Milvus/BYO."
                ),
            ),
        ] = 0,
    ) -> str:
        """Prehľadaj znalostnú bázu. Fan-out na všetky indexy paralelne a zlúči výsledky.

        Vracia dokumenty s úryvkami (bez per-chunk skóre). Pri viacerých indexoch sa top_k
        uplatní na každý index zvlášť, takže celkový počet dokumentov môže prekročiť top_k.
        """
        target_ids = await _get_index_ids()
        logger.info(
            "znalostna_baza_vyhladaj query=%r top_k=%s labels=%s mode=%s adj=%s index_ids=%s",
            query,
            top_k,
            labels,
            retrieval_mode,
            amount_adjacent_snippets,
            target_ids,
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
                for iid in target_ids
            ],
            return_exceptions=True,
        )

        seen_doc_ids: set[int] = set()
        all_docs: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for iid, result in zip(target_ids, raw_results):
            if isinstance(result, Exception):
                logger.warning("znalostna_baza_vyhladaj error (index_id=%s): %s", iid, result)
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

    @mcp.tool(name="znalostna_baza_detail_dokumentu", annotations=read_only)
    async def get_document(
        document_id: Annotated[
            int, pydantic.Field(ge=1, description="ID dokumentu z vyhľadávania alebo zoznamu.")
        ],
        include_chunks: Annotated[  # noqa: FBT002 — MCP tools are keyword-called by the LLM
            bool,
            pydantic.Field(
                description="Ak true, dotiahni aj celý zoznam chunkov dokumentu (len BYO/Milvus).",
            ),
        ] = True,
    ) -> str:
        """Vráť úplné metadáta a (voliteľne) chunky jedného dokumentu."""
        logger.info(
            "znalostna_baza_detail_dokumentu document_id=%s include_chunks=%s",
            document_id,
            include_chunks,
        )
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
            logger.warning("znalostna_baza_detail_dokumentu error: %s", exc)
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

    @mcp.tool(name="znalostna_baza_stitky", annotations=read_only)
    async def list_labels(
        refresh: Annotated[  # noqa: FBT002 — MCP tools are keyword-called by the LLM
            bool,
            pydantic.Field(description="Obíď cache a prepočítaj z indexera."),
        ] = False,
    ) -> str:
        """Vypíš distinct štítky v znalostnej báze (užitočné na objavovanie filtrov)."""
        logger.info("znalostna_baza_stitky refresh=%s", refresh)
        if refresh:
            await labels_cache.invalidate()

        async def _compute() -> dict[str, Any]:
            labels_seen: set[str] = set()
            document_count = 0
            page_size = 100
            for iid in await _get_index_ids():
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
            logger.warning("znalostna_baza_stitky error: %s", exc)
            return _error("indexer_request_failed", exc)
        return _json(payload)
