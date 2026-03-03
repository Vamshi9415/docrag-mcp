"""HTTP download service with retry logic and caching.

Downloads are cached in the ``download_cache`` layer so repeated
requests for the same URL are served from memory.  Transient HTTP
errors trigger up to 3 retries with exponential back-off.

Uses **httpx.AsyncClient** for truly non-blocking I/O — no thread-pool
needed, so the event loop stays free to serve other requests while a
download is in flight.
"""

from __future__ import annotations

import asyncio

import httpx

from mcp_server.core.errors import DownloadError
from mcp_server.core.logging import get_logger
from mcp_server.services.cache import get_cached_download, put_cached_download

logger = get_logger("downloader")

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 5]  # seconds

# Module-level async client — reuses TCP connections across downloads.
# Created lazily on first use so the event loop exists.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return (or create) the module-level ``httpx.AsyncClient``."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": "MCP-RAG-Server/2.0"},
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
    return _client


async def close_client() -> None:
    """Shut down the shared HTTP client (call during server lifespan shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None


async def download(url: str) -> bytes:
    """Download *url* and return raw bytes (cached after first fetch)."""
    cached = get_cached_download(url)
    if cached is not None:
        logger.info("download.cache_hit", extra={"url": url[:80]})
        return cached

    client = _get_client()
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content

            put_cached_download(url, data)
            logger.info(
                "download.success",
                extra={"url": url[:80], "bytes": len(data), "attempt": attempt + 1},
            )
            return data

        except httpx.HTTPStatusError as exc:
            # 4xx errors are client errors (bad URL, auth, not found) — no
            # point retrying.  Only 5xx server errors are transient.
            if exc.response.status_code < 500:
                raise DownloadError(
                    f"HTTP {exc.response.status_code} for {url}: {exc}"
                ) from exc
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.warning(
                    "download.retry",
                    extra={
                        "url": url[:80],
                        "attempt": attempt + 1,
                        "wait": wait,
                    },
                )
                await asyncio.sleep(wait)

        except (httpx.RequestError, httpx.TimeoutException) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                logger.warning(
                    "download.retry",
                    extra={
                        "url": url[:80],
                        "attempt": attempt + 1,
                        "wait": wait,
                    },
                )
                await asyncio.sleep(wait)

    raise DownloadError(
        f"Failed to download {url} after {MAX_RETRIES} attempts: {last_error}"
    )
