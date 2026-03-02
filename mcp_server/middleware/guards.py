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

class AuthMiddleware:
    """ASGI middleware that enforces ``x-api-key`` authentication on the
    MCP streamable-http transport.

    Wrapped around ``mcp.streamable_http_app()`` so that every HTTP request
    — tool calls, SSE streams, and preflight pings — must carry a valid
    ``x-api-key`` header when ``MCP_API_KEY`` is configured in the
    environment.  Auth is skipped when ``MCP_API_KEY`` is not set.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and security_config.auth_enabled:
            headers = dict(scope.get("headers", []))
            provided_key = headers.get(b"x-api-key", b"").decode()
            if provided_key != security_config.api_key:
                logger.warning(
                    "auth.rejected",
                    extra={"detail": "invalid or missing x-api-key on MCP transport"},
                )
                # Return a plain HTTP 401 without invoking the MCP app
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


