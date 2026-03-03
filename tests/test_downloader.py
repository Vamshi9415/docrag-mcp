"""Tests for mcp_server.services.downloader — async httpx downloads."""

from __future__ import annotations

import pytest
import httpx

from mcp_server.services.downloader import download, close_client, _get_client
from mcp_server.services.cache import download_cache
from mcp_server.core.errors import DownloadError


class TestHttpxClient:

    def test_get_client_returns_async_client(self):
        client = _get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed

    def test_get_client_singleton(self):
        c1 = _get_client()
        c2 = _get_client()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_close_client(self):
        _get_client()  # ensure created
        await close_client()
        # After close, next call creates a new instance
        c = _get_client()
        assert not c.is_closed


class TestDownload:

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """If data is already in cache, download() returns it without HTTP."""
        url = "http://test-cache-hit.example.com/file.txt"
        download_cache.put(url, b"cached-content", size_bytes=14)
        result = await download(url)
        assert result == b"cached-content"
        # Clean up
        download_cache.clear()

    @pytest.mark.asyncio
    async def test_invalid_url_raises(self):
        """Requesting an unreachable host should raise DownloadError."""
        with pytest.raises(DownloadError):
            await download("http://this-host-does-not-exist-xyz123.invalid/file.txt")

    @pytest.mark.asyncio
    async def test_download_real_url(self):
        """Integration test: download a tiny known file (httpbin).
        
        Skipped if no internet connectivity.
        """
        url = "https://httpbin.org/bytes/64"
        try:
            data = await download(url)
            assert len(data) == 64
            assert isinstance(data, bytes)
        except DownloadError:
            pytest.skip("No internet connectivity — skipped real download test")
        finally:
            download_cache.clear()

    @pytest.mark.asyncio
    async def test_4xx_errors_fail_immediately(self):
        """4xx client errors (404, 403, etc.) should NOT be retried.
        
        The download should raise DownloadError immediately without
        waiting through the retry backoff delay (9s total).
        """
        import time
        url = "https://httpbin.org/status/404"
        start = time.monotonic()
        try:
            await download(url)
            pytest.fail("Expected DownloadError for 404")
        except DownloadError as exc:
            elapsed = time.monotonic() - start
            assert "404" in str(exc)
            # Should fail fast — well under the 9s total retry backoff
            assert elapsed < 5.0, f"4xx took {elapsed:.1f}s — should not have retried"
        except Exception:
            pytest.skip("No internet connectivity or httpbin unavailable")
        finally:
            download_cache.clear()
