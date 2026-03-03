"""Query tools — document processing and content extraction.

Every tool is wrapped with ``@guarded()`` which provides authentication,
rate limiting, timeout enforcement, structured logging, and error handling.

NOTE: This server is a pure deterministic tool bridge.  It does NOT
contain any LLM, agent, or reasoning logic.  External MCP clients bring
their own LLM and call these tools as needed.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from typing import Any, List

from mcp_server.server import mcp
from mcp_server.middleware import guarded
from mcp_server.middleware.guards import validate_url, validate_text

from mcp_server.services.downloader import download
from mcp_server.services.language import get_language_name
from mcp_server.services.chunking import AdaptiveChunkingStrategy
from mcp_server.services.retrieval import EnhancedRetriever
from mcp_server.services.cache import (
    get_cached_document, put_cached_document,
    get_retriever_with_disk_fallback, put_retriever_with_disk,
)
from mcp_server.core.models import get_embeddings_fast, get_embeddings_accurate
from mcp_server.core.concurrency import run_in_gpu_pool, coalesced_build

from mcp_server.processors import detect_document_type, TargetedDocumentProcessor
from mcp_server.core.logging import get_logger

_logger = get_logger("rag_pipeline")


def _sanitize(text: str) -> str:
    """Remove null bytes and control characters that break JSON serialization."""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


# ───────────────────────────────────────────────────────────────────
# Tool 1 — Process / extract document content
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=300)
async def process_document(document_url: str) -> dict[str, Any]:
    """
    Download and process a document, returning its extracted textual content
    and metadata.  No question-answering is performed — use this to inspect
    what the pipeline extracts.

    Args:
        document_url: Public URL of the document.

    Returns:
        Dict with 'content', 'metadata', 'tables', 'images', 'urls', and
        'detected_language'.
    """
    validate_url(document_url)

    request_id = str(uuid.uuid4())
    doc_content = await download(document_url)
    doc_type = detect_document_type(document_url)

    processed = await TargetedDocumentProcessor.process_document(
        doc_content, doc_type, document_url, request_id
    )

    return {
        "content": _sanitize(processed.content[:50_000]),
        "content_length": len(processed.content),
        "metadata": processed.metadata,
        "tables": [
            {"content": t.content[:5000], "type": t.table_type, "location": t.location}
            for t in processed.tables
        ],
        "images": [
            {"ocr_text": img.ocr_text, "confidence": img.confidence}
            for img in processed.images
        ],
        "urls": [
            {"url": u.url, "type": u.url_type, "context": u.context[:200]}
            for u in processed.extracted_urls
        ],
        "detected_language": processed.detected_language,
        "detected_language_name": get_language_name(processed.detected_language),
    }


# ───────────────────────────────────────────────────────────────────
# Tool 2 — Chunk a document for RAG
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=300)
async def chunk_document(document_url: str) -> dict[str, Any]:
    """
    Download a document, extract its content, and split it into scored
    chunks suitable for RAG retrieval.

    Returns the list of chunks with metadata (index, importance score,
    content type).  The external agent/LLM can use these chunks as
    context for answering questions.

    Args:
        document_url: Public URL of the document to chunk.

    Returns:
        Dict with 'chunks' list, 'chunk_count', and 'document_type'.
    """
    validate_url(document_url)

    request_id = str(uuid.uuid4())
    doc_content = await download(document_url)
    doc_type = detect_document_type(document_url)

    processed = await TargetedDocumentProcessor.process_document(
        doc_content, doc_type, document_url, request_id
    )

    if not processed.content.strip():
        return {"chunks": [], "chunk_count": 0, "document_type": doc_type}

    chunks = AdaptiveChunkingStrategy.create_chunks(processed.content, doc_type)

    return {
        "chunks": [
            {
                "text": c.page_content[:5000],
                "chunk_index": c.metadata.get("chunk_index"),
                "total_chunks": c.metadata.get("total_chunks"),
                "importance_score": c.metadata.get("importance_score"),
                "content_type": c.metadata.get("content_type"),
            }
            for c in chunks
        ],
        "chunk_count": len(chunks),
        "document_type": doc_type,
    }


# ───────────────────────────────────────────────────────────────────
# Tool 3 — Retrieve relevant chunks (vector search, no LLM)
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=300)
async def retrieve_chunks(
    document_url: str, query: str, top_k: int = 5
) -> dict[str, Any]:
    """
    Download and process a document, build a FAISS vector index, and
    return the top-K most relevant chunks for the given query.

    This is pure vector retrieval + cross-encoder reranking.  No LLM
    is involved — the external agent should use the returned chunks as
    context for its own LLM call.

    Args:
        document_url: Public URL of the document.
        query: The search query / question to find relevant chunks for.
        top_k: Number of top chunks to return (default 5, max 20).

    Returns:
        Dict with 'results' (list of chunk dicts with text, score, metadata)
        and 'total_chunks_indexed'.
    """
    validate_url(document_url)
    validate_text(query, "query")
    top_k = max(1, min(top_k, 20))

    request_id = str(uuid.uuid4())
    url_hash = hashlib.sha256(document_url.encode()).hexdigest()[:16]

    _logger.info("rag.step.1.start — checking document cache",
                 extra={"tool": "retrieve_chunks", "url": document_url})

    # Process document (cached)
    processed = get_cached_document(url_hash)
    if processed is not None:
        _logger.info("rag.step.1.cache_hit — using cached document",
                     extra={"tool": "retrieve_chunks"})
    else:
        _logger.info("rag.step.2.download — fetching document",
                     extra={"tool": "retrieve_chunks", "url": document_url})
        doc_content = await download(document_url)
        doc_type = detect_document_type(document_url)

        _logger.info("rag.step.3.extract — processing document",
                     extra={"tool": "retrieve_chunks",
                            "detail": f"doc_type={doc_type}, size={len(doc_content)} bytes"})
        processed = await TargetedDocumentProcessor.process_document(
            doc_content, doc_type, document_url, request_id
        )
        put_cached_document(url_hash, processed)

        _logger.info("rag.step.3.extract.done — text extracted",
                     extra={"tool": "retrieve_chunks",
                            "detail": f"content_length={len(processed.content)} chars, "
                                      f"tables={len(processed.tables)}, "
                                      f"language={processed.detected_language}"})

    if not processed.content.strip():
        _logger.info("rag.step.abort — no content extracted",
                     extra={"tool": "retrieve_chunks"})
        return {"results": [], "total_chunks_indexed": 0}

    # Build retriever (memory cache → disk → build fresh)
    # Disk loading auto-selects the correct embedding model from saved metadata.
    retriever, source = get_retriever_with_disk_fallback(url_hash)
    if retriever is not None:
        total_chunks = len(retriever.chunks)
        _logger.info("rag.step.4.retriever_cache_hit",
                     extra={"tool": "retrieve_chunks",
                            "detail": f"reusing {source} FAISS index with {total_chunks} chunks"})
    else:
        # ── Coalesced build: only ONE coroutine builds per url_hash ──
        async def _build_index():
            # Re-check cache (another coroutine may have finished building)
            ret, src = get_retriever_with_disk_fallback(url_hash)
            if ret is not None:
                return ret, len(ret.chunks)

            doc_type = detect_document_type(document_url)

            _logger.info("rag.step.4.chunking — splitting into chunks",
                         extra={"tool": "retrieve_chunks", "detail": f"doc_type={doc_type}"})
            chunks = AdaptiveChunkingStrategy.create_chunks(processed.content, doc_type)

            if not chunks:
                return None, 0

            _logger.info("rag.step.4.chunking.done",
                         extra={"tool": "retrieve_chunks",
                                "detail": f"created {len(chunks)} chunks"})

            emb_model = "fast" if len(chunks) <= 50 else "accurate"
            emb = get_embeddings_fast() if len(chunks) <= 50 else get_embeddings_accurate()

            _logger.info("rag.step.5.embedding — building FAISS vector index",
                         extra={"tool": "retrieve_chunks",
                                "detail": f"model={emb_model}, chunks={len(chunks)}"})

            # GPU-semaphore-guarded FAISS build
            ret = await run_in_gpu_pool(EnhancedRetriever, emb, chunks, emb_model)
            put_retriever_with_disk(url_hash, ret)

            _logger.info("rag.step.5.embedding.done — FAISS index built & persisted to disk",
                         extra={"tool": "retrieve_chunks",
                                "detail": f"indexed {len(chunks)} chunks"})
            return ret, len(chunks)

        retriever, total_chunks = await coalesced_build(url_hash, _build_index)
        if retriever is None:
            _logger.info("rag.step.abort — no chunks created",
                         extra={"tool": "retrieve_chunks"})
            return {"results": [], "total_chunks_indexed": 0}

    # Retrieve — GPU-semaphore-guarded vector search + rerank
    _logger.info("rag.step.6.retrieval — vector search + rerank",
                 extra={"tool": "retrieve_chunks",
                        "detail": f"query='{query[:100]}', top_k={top_k}"})

    results = await run_in_gpu_pool(retriever.retrieve, query, top_k)

    _logger.info("rag.step.6.retrieval.done",
                 extra={"tool": "retrieve_chunks",
                        "detail": f"returned {len(results)} results from {total_chunks} chunks"})

    return {
        "results": [
            {
                "text": r.page_content,
                "chunk_index": r.metadata.get("chunk_index"),
                "importance_score": r.metadata.get("importance_score"),
                "content_type": r.metadata.get("content_type"),
            }
            for r in results
        ],
        "total_chunks_indexed": total_chunks,
    }


# ───────────────────────────────────────────────────────────────────
# Tool — Pandas-based spreadsheet query (XLSX / CSV)
# ───────────────────────────────────────────────────────────────────

def _pandas_search(file_content: bytes, file_type: str, search_value: str,
                   return_columns: list[str] | None = None,
                   max_rows: int = 20) -> list[dict]:
    """Search tabular data for rows containing *search_value*.

    Performs case-insensitive substring matching across ALL columns.
    Returns matching rows as a list of dicts.
    """
    import io
    import pandas as pd

    needle = search_value.lower().strip()
    matches: list[dict] = []

    if file_type == "csv":
        sheets = {"CSV": pd.read_csv(io.BytesIO(file_content), dtype=str).fillna("")}
    else:
        xls = pd.ExcelFile(io.BytesIO(file_content), engine="openpyxl")
        sheets = {}
        for name in xls.sheet_names:
            df = xls.parse(name, header=None, dtype=str).fillna("")
            if df.empty:
                continue
            # Detect header row
            from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor
            header_row, _ = EnhancedXLSXTableExtractor._detect_header(df)
            if header_row >= 0:
                headers = [str(h).strip() or f"Col_{i}" for i, h in enumerate(df.iloc[header_row])]
                df = df.iloc[header_row + 1:].reset_index(drop=True)
                df.columns = headers[:len(df.columns)]
            else:
                df.columns = [f"Col_{i}" for i in range(len(df.columns))]
            sheets[name] = df

    for sheet_name, df in sheets.items():
        # Filter to requested columns if specified
        if return_columns:
            available = [c for c in return_columns if c in df.columns]
            if not available:
                # Try case-insensitive match
                col_map = {c.lower(): c for c in df.columns}
                available = [col_map[c.lower()] for c in return_columns if c.lower() in col_map]
            search_df = df  # search all columns
        else:
            available = list(df.columns)
            search_df = df

        # Case-insensitive substring match across all columns
        mask = search_df.apply(
            lambda row: any(needle in str(v).lower() for v in row), axis=1
        )
        hits = df.loc[mask, available] if available else df.loc[mask]

        for _, row in hits.head(max_rows).iterrows():
            record = row.to_dict()
            if len(sheets) > 1:
                record["_sheet"] = sheet_name
            matches.append(record)

        if len(matches) >= max_rows:
            break

    return matches[:max_rows]


@mcp.tool()
@guarded(timeout=120)
async def query_spreadsheet(
    document_url: str,
    search_value: str,
    return_columns: str = "",
) -> dict[str, Any]:
    """
    Download an XLSX or CSV file and search for rows matching a value using
    pandas.  This is much more precise than vector search for finding
    specific records in structured tabular data.

    Use this tool when the user asks for specific cell-level data from
    a spreadsheet — e.g. "phone number of John", "email of Alice",
    "revenue of Q3".

    Args:
        document_url: Public URL of the XLSX or CSV file.
        search_value: Text value to search for (case-insensitive substring
                      match across ALL columns).  E.g. "vamshi bachu".
        return_columns: Comma-separated column names to return.
                        Leave empty to return ALL columns of matching rows.

    Returns:
        Dict with 'matches' (list of row dicts), 'match_count', 'columns',
        and 'sheets_searched'.
    """
    validate_url(document_url)
    validate_text(search_value, "search_value")

    request_id = str(uuid.uuid4())
    doc_content = await download(document_url)
    doc_type = detect_document_type(document_url)

    if doc_type not in ("xlsx", "csv"):
        return {"error": f"query_spreadsheet only supports XLSX/CSV files, got '{doc_type}'."}

    cols = [c.strip() for c in return_columns.split(",") if c.strip()] if return_columns else None

    _logger.info("spreadsheet.query — pandas lookup",
                 extra={"tool": "query_spreadsheet",
                        "detail": f"search='{search_value}', doc_type={doc_type}"})

    loop = asyncio.get_running_loop()
    matches = await loop.run_in_executor(
        None, lambda: _pandas_search(doc_content, doc_type, search_value, cols)
    )

    _logger.info("spreadsheet.query.done",
                 extra={"tool": "query_spreadsheet",
                        "detail": f"found {len(matches)} matching rows"})

    # Sanitize all values
    clean_matches = []
    for row in matches:
        clean_matches.append({k: _sanitize(str(v)) for k, v in row.items()})

    return {
        "matches": clean_matches,
        "match_count": len(clean_matches),
        "columns": list(clean_matches[0].keys()) if clean_matches else [],
        "search_value": search_value,
    }
