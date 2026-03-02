"""ASGI app factory for uvicorn --reload mode.

Used by ``python -m mcp_server --reload`` so that uvicorn can re-import
the module on each code change.
"""

from __future__ import annotations


def create_app():
    """Return the Starlette/ASGI app that FastMCP builds for streamable-http.

    In reload mode the FastMCP lifespan (server.py) is never triggered by
    ``streamable_http_app()``, so we pre-load models here instead.
    __pycache__ is excluded from reload watching (see __main__.py) to
    prevent an infinite reload loop.
    """
    from mcp_server.server import mcp
    from mcp_server.core.models import _ensure_models_loaded
    from mcp_server.core.logging import get_logger

    logger = get_logger("server")
    logger.info("server.startup (reload mode)")
    logger.info("server.models.loading — pre-loading embedding & reranker models")
    _ensure_models_loaded()
    logger.info("server.models.ready — all models loaded and ready")

    from mcp_server.middleware.guards import AuthMiddleware
    return AuthMiddleware(mcp.streamable_http_app())
