"""FastMCP server instance, lifespan management, and tool / resource registration.

This module is the single source of the ``mcp`` object that every tool
and resource module imports.  Importing ``server.py`` is side-effect-free;
the heavy tool/resource registration happens via the bottom-of-file imports
that are executed once when the package is first loaded.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_server.core.logging import setup_logging, get_logger
from mcp_server.services.cache import clear_all as _clear_all_caches

# ── Logging bootstrap ─────────────────────────────────────────────
setup_logging()
logger = get_logger("server")


# ── Lifespan (startup / shutdown) ─────────────────────────────────

@asynccontextmanager
async def _lifespan(server: FastMCP):
    """Server startup and graceful shutdown."""
    logger.info("server.startup", extra={"version": "2.0.0"})

    # ── Eagerly load embedding + reranker models at startup ───────
    import asyncio
    loop = asyncio.get_running_loop()
    from mcp_server.core.models import _ensure_models_loaded
    logger.info("server.models.loading — pre-loading embedding & reranker models")
    await loop.run_in_executor(None, _ensure_models_loaded)
    logger.info("server.models.ready — all models loaded and ready")

    yield
    logger.info("server.shutdown — flushing download cache (keeping doc/retriever cache)")
    # Only clear the download layer; keep document & retriever caches
    # so restart/reconnect stays fast.
    from mcp_server.services.cache import download_cache
    download_cache.clear()
    logger.info("server.shutdown.complete")


# ── FastMCP instance (default host/port; overridden by create_server) ──

mcp = FastMCP(
    "RAG Document Server",
    instructions=(
        "Pure deterministic MCP tool server for document processing. "
        "Supports PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, and image (OCR). "
        "This server does NOT contain any LLM or agentic logic. "
        "Use 'process_document' to extract content, 'chunk_document' to split "
        "into RAG-ready chunks, 'retrieve_chunks' for vector search, or "
        "individual 'extract_*' tools for format-specific extraction. "
        "The calling agent should provide its own LLM for reasoning."
    ),
    lifespan=_lifespan,
)


def create_server(host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    """Return the singleton ``mcp`` instance with overridden host/port.

    FastMCP stores host/port on ``mcp.settings``.  We mutate them here
    so the CLI ``--host`` / ``--port`` flags take effect.
    """
    mcp.settings.host = host
    mcp.settings.port = port
    return mcp


# ── Register tools and resources via side-effect imports ──────────
# Each module uses ``@mcp.tool()`` / ``@mcp.resource()`` decorators.

import mcp_server.tools.query      # noqa: F401, E402
import mcp_server.tools.extract    # noqa: F401, E402
import mcp_server.tools.utility    # noqa: F401, E402
import mcp_server.resources        # noqa: F401, E402
