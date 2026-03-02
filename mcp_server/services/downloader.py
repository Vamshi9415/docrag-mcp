"""HTTP download service with retry logic and caching.

Downloads are cached in the ``download_cache`` layer so repeated
requests for the same URL are served from memory.  Transient HTTP
errors trigger up to 3 retries with exponential back-off.
"""

from __future__ import annotations

import asyncio

import requests

from mcp_server.core.errors import DownloadError
from mcp_server.core.logging import get_logger
from mcp_server.services.cache import get_cached_download, put_cached_download

logger = get_logger("downloader")

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 5]  # seconds


async def download(url: str) -> bytes:
    """Download *url* and return raw bytes (cached after first fetch)."""
    cached = get_cached_download(url)
    if cached is not None:
        logger.info("download.cache_hit", extra={"url": url[:80]})
        return cached

    loop = asyncio.get_running_loop()
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    url,
                    timeout=60,
                    headers={"User-Agent": "MCP-RAG-Server/2.0"},
                ),
            )
            resp.raise_for_status()
            data = resp.content

            put_cached_download(url, data)
            logger.info(
                "download.success",
                extra={"url": url[:80], "bytes": len(data), "attempt": attempt + 1},
            )
            return data

        except Exception as exc:
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
