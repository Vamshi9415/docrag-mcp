"""Utility tools — language detection, system health, and cache management.

These are pure deterministic tools — no LLM involved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp_server.server import mcp
from mcp_server.middleware import guarded
from mcp_server.middleware.guards import validate_text

from mcp_server.core.models import models_loaded
from mcp_server.core.config import (
    RERANK_AVAILABLE,
    OCR_AVAILABLE,
    LANG_DETECT_AVAILABLE,
    DEVICE,
    server_config,
    security_config,
)
from mcp_server.services.language import detect_language_robust, get_language_name
from mcp_server.services.cache import clear_all as clear_cache, cache_stats


# ───────────────────────────────────────────────────────────────────
# Tool 10 — Detect language
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=30)
async def detect_language(text: str) -> dict[str, str]:
    """
    Detect the language of a piece of text using multi-round sampling.

    Args:
        text: The text to analyse (at least 10 characters recommended).

    Returns:
        Dict with 'language_code' (ISO 639-1) and 'language_name'.
    """
    validate_text(text, "text")

    code = detect_language_robust(text)
    return {
        "language_code": code,
        "language_name": get_language_name(code),
    }


# ───────────────────────────────────────────────────────────────────
# Tool 11 — System health / capabilities
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=30)
async def get_system_health() -> dict[str, Any]:
    """
    Return the health status, loaded models, supported formats, and
    capabilities of the RAG system.

    Returns:
        Comprehensive health report dict.
    """
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


# ───────────────────────────────────────────────────────────────────
# Tool 12 — Cache management
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=30)
async def manage_cache(action: str = "stats") -> dict[str, Any]:
    """
    Inspect or clear the server-side document / index cache.

    The server caches downloaded files, processed documents, and FAISS
    retrievers so repeated queries on the same URL are fast.

    Args:
        action: One of 'stats' (default) or 'clear'.

    Returns:
        Dict with cache statistics or confirmation of cleared entries.
    """
    if action == "clear":
        result = clear_cache()
        return {"action": "clear", **result}
    return {"action": "stats", **cache_stats()}
