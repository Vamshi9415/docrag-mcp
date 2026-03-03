"""Plain REST API — FastAPI wrapper around the existing service layer.

Run with:
    python -m mcp_server --transport rest
    python -m mcp_server --transport rest --host 0.0.0.0 --port 9000

This is a pure deterministic tool server.  It does NOT contain any LLM,
agent, or reasoning logic.  Every endpoint is a normal JSON POST/GET
route — no sessions, no SSE, no MCP protocol.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mcp_server.core.config import (
    RERANK_AVAILABLE, OCR_AVAILABLE, LANG_DETECT_AVAILABLE,
    DEVICE, server_config, security_config, TEMP_FILES_PATH,
)
from mcp_server.core.errors import MCPServerError
from mcp_server.core.logging import setup_logging, get_logger, request_id_var
from mcp_server.core.models import models_loaded, get_embeddings_fast, get_embeddings_accurate, _ensure_models_loaded
from mcp_server.middleware.guards import (
    check_rate_limit, validate_url, validate_text,
)
from mcp_server.services.cache import (
    clear_all as clear_cache, cache_stats,
    get_cached_document, put_cached_document,
    get_retriever_with_disk_fallback, put_retriever_with_disk,
)
from mcp_server.services.downloader import download, close_client
from mcp_server.services.language import detect_language_robust, get_language_name
from mcp_server.services.chunking import AdaptiveChunkingStrategy
from mcp_server.services.retrieval import EnhancedRetriever
from mcp_server.core.concurrency import run_in_gpu_pool, coalesced_build
from mcp_server.processors import detect_document_type, TargetedDocumentProcessor

setup_logging()
logger = get_logger("api")


# ── Lifespan — startup / shutdown hooks ───────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load ML models on startup; close httpx client on shutdown."""
    logger.info("api.startup — pre-loading ML models")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _ensure_models_loaded)
    logger.info("api.startup — models ready")
    yield
    logger.info("api.shutdown — closing httpx client")
    await close_client()
    logger.info("api.shutdown — done")


# ── FastAPI app ───────────────────────────────────────────────────

app = FastAPI(
    title="RAG Document Server — REST API",
    version="2.0.0",
    description=(
        "Pure deterministic REST API for document processing, chunking, "
        "and vector retrieval. No LLM inside — bring your own agent. "
        "Supports PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, and image (OCR)."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve uploaded files as static assets ─────────────────────────
_UPLOADS_DIR = os.path.join(TEMP_FILES_PATH, "uploads")
os.makedirs(_UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=_UPLOADS_DIR), name="uploads")


# ── Middleware: auth, rate-limit, request-id ────────────────────

@app.middleware("http")
async def guard_middleware(request: Request, call_next):
    rid = uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    try:
        api_key = request.headers.get("x-api-key", "")
        if security_config.auth_enabled:
            if api_key != security_config.api_key:
                return JSONResponse(
                    status_code=401,
                    content={"error": "Invalid or missing API key", "code": "AUTH_ERROR"},
                )
        try:
            check_rate_limit(request.url.path, api_key=api_key)
        except MCPServerError as exc:
            return JSONResponse(
                status_code=429,
                content={"error": exc.message, "code": exc.code},
            )
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(MCPServerError)
async def mcp_error_handler(_request: Request, exc: MCPServerError):
    return JSONResponse(status_code=400, content={"error": exc.message, "code": exc.code})


# ═════════════════════════════════════════════════════════════════
#  Request models
# ═════════════════════════════════════════════════════════════════

class DocumentUrlRequest(BaseModel):
    document_url: str

class RetrieveChunksRequest(BaseModel):
    document_url: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)

class ExtractRequest(BaseModel):
    document_url: str

class DetectLanguageRequest(BaseModel):
    text: str

class ManageCacheRequest(BaseModel):
    action: str = Field(default="stats", description="One of 'stats' or 'clear'")


# ═════════════════════════════════════════════════════════════════
#  Document Processing endpoints
# ═════════════════════════════════════════════════════════════════

@app.post("/api/process-document", tags=["Document"])
async def api_process_document(body: DocumentUrlRequest) -> dict[str, Any]:
    """Extract text, tables, images, URLs from any supported document."""
    validate_url(body.document_url)

    request_id = str(uuid.uuid4())
    doc_content = await download(body.document_url)
    doc_type = detect_document_type(body.document_url)

    processed = await TargetedDocumentProcessor.process_document(
        doc_content, doc_type, body.document_url, request_id
    )

    return {
        "content": processed.content[:50_000],
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


@app.post("/api/chunk-document", tags=["Document"])
async def api_chunk_document(body: DocumentUrlRequest) -> dict[str, Any]:
    """Extract and split a document into scored, RAG-ready chunks."""
    validate_url(body.document_url)

    request_id = str(uuid.uuid4())
    doc_content = await download(body.document_url)
    doc_type = detect_document_type(body.document_url)

    processed = await TargetedDocumentProcessor.process_document(
        doc_content, doc_type, body.document_url, request_id
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


@app.post("/api/retrieve-chunks", tags=["Document"])
async def api_retrieve_chunks(body: RetrieveChunksRequest) -> dict[str, Any]:
    """Vector search (FAISS + reranking) to find the most relevant chunks."""
    validate_url(body.document_url)
    validate_text(body.query, "query")

    request_id = str(uuid.uuid4())
    url_hash = hashlib.sha256(body.document_url.encode()).hexdigest()[:16]

    processed = get_cached_document(url_hash)
    if processed is None:
        doc_content = await download(body.document_url)
        doc_type = detect_document_type(body.document_url)
        processed = await TargetedDocumentProcessor.process_document(
            doc_content, doc_type, body.document_url, request_id
        )
        put_cached_document(url_hash, processed)

    if not processed.content.strip():
        return {"results": [], "total_chunks_indexed": 0}

    # Disk loading auto-selects the correct embedding model from saved metadata.
    retriever, source = get_retriever_with_disk_fallback(url_hash)
    if retriever is None:
        # Coalesced build — only one coroutine builds per url_hash
        async def _build_index():
            # Re-check cache (another coroutine may have finished)
            ret, src = get_retriever_with_disk_fallback(url_hash)
            if ret is not None:
                return ret, len(ret.chunks)

            doc_type = detect_document_type(body.document_url)
            chunks = AdaptiveChunkingStrategy.create_chunks(processed.content, doc_type)
            if not chunks:
                return None, 0
            emb_model = "fast" if len(chunks) <= 50 else "accurate"
            emb = get_embeddings_fast() if len(chunks) <= 50 else get_embeddings_accurate()
            ret = await run_in_gpu_pool(EnhancedRetriever, emb, chunks, emb_model)
            put_retriever_with_disk(url_hash, ret)
            return ret, len(chunks)

        retriever, total_chunks = await coalesced_build(url_hash, _build_index)
        if retriever is None:
            return {"results": [], "total_chunks_indexed": 0}
    else:
        total_chunks = len(retriever.chunks)

    results = await run_in_gpu_pool(retriever.retrieve, body.query, body.top_k)

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


# ═════════════════════════════════════════════════════════════════
#  Extraction endpoints
# ═════════════════════════════════════════════════════════════════

@app.post("/api/extract/pdf", tags=["Extract"])
async def api_extract_pdf(body: ExtractRequest) -> dict[str, Any]:
    """PDF text extraction with layout preservation."""
    validate_url(body.document_url)
    from mcp_server.processors.pdf import EnhancedPDFProcessor
    doc = await download(body.document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, EnhancedPDFProcessor.extract_pdf_content, doc)
    return {"text": text[:50_000], "char_count": len(text)}


@app.post("/api/extract/docx", tags=["Extract"])
async def api_extract_docx(body: ExtractRequest) -> dict[str, Any]:
    """DOCX text extraction with headings and tables."""
    validate_url(body.document_url)
    from mcp_server.processors.docx import extract_docx_text as _extract
    doc = await download(body.document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _extract, doc)
    return {"text": text[:50_000], "char_count": len(text)}


@app.post("/api/extract/pptx", tags=["Extract"])
async def api_extract_pptx(body: ExtractRequest) -> dict[str, Any]:
    """PPTX slide text, tables, and speaker notes extraction."""
    validate_url(body.document_url)
    from mcp_server.processors.pptx import EnhancedPPTXTextExtractor
    doc = await download(body.document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, EnhancedPPTXTextExtractor.extract_text_from_pptx, doc)
    return {"text": text[:50_000], "char_count": len(text)}


@app.post("/api/extract/xlsx", tags=["Extract"])
async def api_extract_xlsx(body: ExtractRequest) -> dict[str, Any]:
    """XLSX multi-sheet table extraction."""
    validate_url(body.document_url)
    from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor
    doc = await download(body.document_url)
    loop = asyncio.get_running_loop()
    tables = await loop.run_in_executor(None, EnhancedXLSXTableExtractor.extract_tables_from_xlsx, doc)
    return {
        "tables": [
            {"content": t.content[:5000], "table_type": t.table_type, "location": t.location, "metadata": t.metadata}
            for t in tables
        ],
        "table_count": len(tables),
    }


@app.post("/api/extract/csv", tags=["Extract"])
async def api_extract_csv(body: ExtractRequest) -> dict[str, Any]:
    """CSV tabular content extraction."""
    validate_url(body.document_url)
    from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor
    doc = await download(body.document_url)
    loop = asyncio.get_running_loop()
    tables = await loop.run_in_executor(None, EnhancedXLSXTableExtractor.extract_tables_from_csv, doc)
    return {
        "tables": [
            {"content": t.content[:5000], "table_type": t.table_type, "location": t.location, "metadata": t.metadata}
            for t in tables
        ],
        "table_count": len(tables),
    }


@app.post("/api/extract/image", tags=["Extract"])
async def api_extract_image(body: ExtractRequest) -> dict[str, Any]:
    """OCR via pytesseract."""
    validate_url(body.document_url)
    if not OCR_AVAILABLE:
        raise HTTPException(status_code=501, detail="pytesseract is not installed — OCR unavailable")
    from mcp_server.processors.image import ImageOCRProcessor
    request_id = str(uuid.uuid4())
    doc = await download(body.document_url)
    images = await ImageOCRProcessor.process_image_file(doc, body.document_url, request_id)
    return {
        "ocr_results": [
            {"text": img.ocr_text, "confidence": img.confidence, "metadata": img.metadata}
            for img in images
        ],
    }


# ═════════════════════════════════════════════════════════════════
#  Utility endpoints
# ═════════════════════════════════════════════════════════════════

@app.post("/api/detect-language", tags=["Utility"])
async def api_detect_language(body: DetectLanguageRequest) -> dict[str, str]:
    """Language detection via multi-round sampling."""
    validate_text(body.text, "text")
    code = detect_language_robust(body.text)
    return {"language_code": code, "language_name": get_language_name(code)}


@app.get("/api/health", tags=["Utility"])
async def api_health() -> dict[str, Any]:
    """System health, loaded models, capabilities."""
    return {
        "status": "healthy",
        "version": server_config.version,
        "mode": "deterministic-tools (no LLM)",
        "features": {
            "adaptive_chunking": True,
            "vector_retrieval": True,
            "reranking": RERANK_AVAILABLE,
            "ocr": OCR_AVAILABLE,
            "language_detection": LANG_DETECT_AVAILABLE,
        },
        "security": {
            "auth_enabled": security_config.auth_enabled,
            "rate_limit_rpm": security_config.rate_limit_rpm,
            "request_timeout_s": security_config.request_timeout,
        },
        "models_loaded": models_loaded(),
        "models": {
            "embedding_fast": "sentence-transformers/all-MiniLM-L6-v2",
            "embedding_accurate": "BAAI/bge-small-en-v1.5",
            "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2" if RERANK_AVAILABLE else "not available",
            "llm": "NONE — this is a pure tool server",
        },
        "supported_formats": {
            "documents": ["pdf", "docx", "pptx", "txt", "html"],
            "tables": ["xlsx", "csv"],
            "images": ["png", "jpeg", "jpg"],
        },
        "device": DEVICE,
        "cache": cache_stats(),
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/cache", tags=["Utility"])
async def api_cache(body: ManageCacheRequest) -> dict[str, Any]:
    """Inspect or clear the document/index cache."""
    if body.action == "clear":
        result = clear_cache()
        return {"action": "clear", **result}
    return {"action": "stats", **cache_stats()}


# ═════════════════════════════════════════════════════════════════
#  File Upload endpoint
# ═════════════════════════════════════════════════════════════════

# Allowed extensions for upload (matches server's supported formats)
_ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv",
    ".txt", ".html", ".htm",
    ".png", ".jpg", ".jpeg",
}


@app.post("/api/upload", tags=["Utility"])
async def api_upload_file(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a local file and receive a server-hosted URL.

    This solves the **local file problem**: every tool on this server requires
    a public HTTP URL, but users often have files on their local machine with
    no HTTP server.  This endpoint accepts ``multipart/form-data``, saves the
    file to ``temp_files/uploads/``, and returns a URL that the server can
    download from itself.

    The returned ``document_url`` can be passed directly to any tool
    (``retrieve_chunks``, ``process_document``, ``query_spreadsheet``, etc.).

    Max file size: 50 MB.
    Allowed formats: PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, PNG, JPEG.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Validate extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {', '.join(sorted(_ALLOWED_EXTENSIONS))}",
        )

    # Read file content (enforce 50 MB limit)
    content = await file.read()
    max_size = 50 * 1024 * 1024  # 50 MB
    if len(content) > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / 1024 / 1024:.1f} MB). Max: 50 MB.",
        )

    # Save with a unique name to avoid collisions
    safe_name = f"{uuid.uuid4().hex[:12]}_{file.filename}"
    save_path = os.path.join(_UPLOADS_DIR, safe_name)
    with open(save_path, "wb") as f:
        f.write(content)

    # Build the URL using the request's own host so it works from any client
    base_url = str(request.base_url).rstrip("/")
    document_url = f"{base_url}/uploads/{safe_name}"

    logger.info(
        "upload.success",
        extra={"upload_name": file.filename, "size": len(content), "url": document_url},
    )

    return {
        "filename": file.filename,
        "saved_as": safe_name,
        "size_bytes": len(content),
        "document_url": document_url,
        "usage": "Pass 'document_url' to any tool (retrieve_chunks, process_document, etc.)",
    }


# ── Root listing ───────────────────────────────────────────────

@app.get("/", tags=["Info"])
async def root():
    """List all available endpoints."""
    return {
        "server": "RAG Document Server — Pure Tool API (no LLM)",
        "version": "2.0.0",
        "docs": "/docs",
        "endpoints": {
            "document": {
                "POST /api/process-document": "Extract content from any document",
                "POST /api/chunk-document": "Split into RAG-ready chunks",
                "POST /api/retrieve-chunks": "Vector search for relevant chunks",
            },
            "extract": {
                "POST /api/extract/pdf": "PDF text extraction",
                "POST /api/extract/docx": "DOCX text extraction",
                "POST /api/extract/pptx": "PPTX text extraction",
                "POST /api/extract/xlsx": "XLSX table extraction",
                "POST /api/extract/csv": "CSV table extraction",
                "POST /api/extract/image": "Image OCR",
            },
            "utility": {
                "POST /api/upload": "Upload a local file, get a server-hosted URL",
                "POST /api/detect-language": "Language detection",
                "GET  /api/health": "System health & capabilities",
                "POST /api/cache": "Cache stats or clear",
            },
        },
    }


def create_rest_app() -> FastAPI:
    """Return the configured FastAPI instance."""
    return app
