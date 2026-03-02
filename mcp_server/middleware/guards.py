"""Security guards: authentication, rate limiting, and input validation.

These are pure functions (no I/O) so they can be called synchronously
inside the ``guarded`` decorator without awaiting anything.
"""

from __future__ import annotations

import re
import time
import threading

from mcp_server.core.config import security_config
from mcp_server.core.errors import AuthenticationError, RateLimitError, ValidationError
from mcp_server.core.logging import get_logger

logger = get_logger("guards")

# ═══════════════════════════════════════════════════════════════════
# Authentication
# ═══════════════════════════════════════════════════════════════════

def check_auth() -> None:
    """Verify API key if authentication is enabled (``MCP_API_KEY`` is set).

    In streamable-http transport the key is enforced at the env-var level;
    extend this function to inspect transport-level headers when the MCP SDK
    supports it.
    """
    if not security_config.auth_enabled:
        return
    if not security_config.api_key:
        raise AuthenticationError("MCP_API_KEY is configured but empty")


# ═══════════════════════════════════════════════════════════════════
# Rate Limiting — token-bucket algorithm
# ═══════════════════════════════════════════════════════════════════

class _TokenBucket:
    """Thread-safe token-bucket rate limiter."""

    def __init__(self, rpm: int):
        self.capacity = float(rpm)
        self.tokens = float(rpm)
        self.refill_rate = rpm / 60.0  # tokens per second
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.last_refill) * self.refill_rate,
            )
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


_global_bucket = _TokenBucket(security_config.rate_limit_rpm)


def check_rate_limit(tool_name: str = "") -> None:
    """Consume one token from the global bucket; raise on exhaustion."""
    if not _global_bucket.consume():
        raise RateLimitError(
            f"Rate limit exceeded ({security_config.rate_limit_rpm} req/min). "
            f"Try again shortly."
        )


# ═══════════════════════════════════════════════════════════════════
# Input Validation
# ═══════════════════════════════════════════════════════════════════

_SAFE_URL_RE = re.compile(
    r"^https?://"
    r"[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+"
    r"$"
)


def validate_url(url: str) -> None:
    """Raise ``ValidationError`` if *url* is missing, oversized, or malformed."""
    if not url or not isinstance(url, str):
        raise ValidationError("URL must be a non-empty string")
    if len(url) > security_config.max_url_length:
        raise ValidationError(
            f"URL exceeds maximum length of {security_config.max_url_length}"
        )
    if not _SAFE_URL_RE.match(url):
        raise ValidationError(
            "URL must start with http:// or https:// and contain only valid characters"
        )


def validate_text(text: str, field_name: str = "text") -> None:
    """Raise ``ValidationError`` if *text* is not a string or too long."""
    if not isinstance(text, str):
        raise ValidationError(f"{field_name} must be a string")
    if len(text) > security_config.max_text_length:
        raise ValidationError(
            f"{field_name} exceeds maximum length of {security_config.max_text_length:,} characters"
        )


# ═══════════════════════════════════════════════════════════════════
# ASGI Middleware — HTTP-level auth for MCP streamable-http transport
# ═══════════════════════════════════════════════════════════════════

# Paths that bypass authentication — reachable by infra probes without a key
_AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/health"})


class AuthMiddleware:
    """ASGI middleware that enforces ``x-api-key`` authentication on the
    MCP streamable-http transport.

    Wrapped around ``MCPRouter(mcp.streamable_http_app())`` so that every
    HTTP request — tool calls, SSE streams, and preflight pings — must
    carry a valid ``x-api-key`` header when ``MCP_API_KEY`` is configured.
    Auth is skipped when ``MCP_API_KEY`` is not set.

    ``GET /health`` is always exempt so load-balancers and k8s liveness
    probes can reach it without credentials.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and security_config.auth_enabled:
            path = scope.get("path", "")
            if path not in _AUTH_EXEMPT_PATHS:
                headers = dict(scope.get("headers", []))
                provided_key = headers.get(b"x-api-key", b"").decode()
                if provided_key != security_config.api_key:
                    logger.warning(
                        "auth.rejected",
                        extra={"detail": "invalid or missing x-api-key on MCP transport"},
                    )
                    body = b'{"error": "Invalid or missing API key", "code": "AUTH_ERROR"}'
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    })
                    await send({"type": "http.response.body", "body": body})
                    return
        await self.app(scope, receive, send)


class MCPRouter:
    """Lightweight ASGI router that adds standard GET endpoints to the MCP
    streamable-http transport, then forwards everything else to FastMCP.

    Routes handled here (must sit *inside* AuthMiddleware):

        GET /health   — liveness probe, NO auth required (whitelisted above)
                        Returns: {status, transport, auth_enabled, models_loaded}

        GET /info     — server capabilities, auth required
                        Returns: {server, version, mcp_endpoint, auth, features,
                                  supported_formats, device}

    All other paths/methods → FastMCP ``streamable_http_app()``.

    Deployment stack (outer → inner)::

        AuthMiddleware
            └─ MCPRouter
                ├─ GET /health    (probe — no auth)
                ├─ GET /info      (capabilities — auth)
                └─ *              FastMCP (/mcp POST + SSE)
    """

    def __init__(self, mcp_app) -> None:
        self.mcp_app = mcp_app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            method = scope.get("method", "").upper()

            if path == "/health" and method == "GET":
                await self._health(send)
                return
            if path == "/info" and method == "GET":
                await self._info(send)
                return

        await self.mcp_app(scope, receive, send)

    @staticmethod
    async def _health(send) -> None:
        """GET /health — liveness/readiness probe (no auth)."""
        import json
        from mcp_server.core.models import models_loaded

        body = json.dumps({
            "status": "healthy",
            "transport": "streamable-http",
            "mcp_endpoint": "/mcp",
            "auth_enabled": security_config.auth_enabled,
            "models_loaded": models_loaded(),
        }, indent=2).encode()

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _info(send) -> None:
        """GET /info — server capabilities (auth required)."""
        import json
        from mcp_server.core.config import (
            RERANK_AVAILABLE, OCR_AVAILABLE, LANG_DETECT_AVAILABLE,
            DEVICE, server_config,
        )

        body = json.dumps({
            "server": server_config.name,
            "version": server_config.version,
            "transport": "streamable-http",
            "mcp_endpoint": "/mcp",
            "auth": {
                "enabled": security_config.auth_enabled,
                "header": "x-api-key",
            },
            "features": {
                "adaptive_chunking": True,
                "vector_retrieval": True,
                "reranking": RERANK_AVAILABLE,
                "ocr": OCR_AVAILABLE,
                "language_detection": LANG_DETECT_AVAILABLE,
            },
            "supported_formats": [
                "pdf", "docx", "pptx", "xlsx", "csv",
                "txt", "html", "png", "jpeg",
            ],
            "device": DEVICE,
        }, indent=2).encode()

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


