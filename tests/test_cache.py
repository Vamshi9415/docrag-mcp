"""Tests for mcp_server.services.cache — TTL cache layers."""

from __future__ import annotations

import time
import pytest

from mcp_server.services.cache import (
    _TTLCache,
    download_cache,
    document_cache,
    retriever_cache,
    get_cached_download,
    put_cached_download,
    get_cached_document,
    put_cached_document,
    cache_stats,
    clear_all,
)


# ═══════════════════════════════════════════════════════════════════
# _TTLCache unit tests
# ═══════════════════════════════════════════════════════════════════

class TestTTLCache:

    def _make(self, **kw):
        defaults = dict(max_entries=5, ttl=10, name="test")
        defaults.update(kw)
        return _TTLCache(**defaults)

    def test_put_and_get(self):
        c = self._make()
        c.put("k1", "v1")
        assert c.get("k1") == "v1"

    def test_miss_returns_none(self):
        c = self._make()
        assert c.get("nonexistent") is None

    def test_expiry(self):
        c = self._make(ttl=1)
        c.put("k1", "v1")
        assert c.get("k1") == "v1"
        # Manually expire the entry
        entry = c._store["k1"]
        entry.expires_at = time.time() - 1
        assert c.get("k1") is None

    def test_max_entries_eviction(self):
        c = self._make(max_entries=3)
        c.put("a", 1)
        c.put("b", 2)
        c.put("c", 3)
        c.put("d", 4)  # should evict the oldest
        assert c.get("d") == 4
        # One entry should have been evicted
        entries = [c.get(k) for k in ("a", "b", "c")]
        assert entries.count(None) >= 1

    def test_overwrite_same_key(self):
        c = self._make()
        c.put("k", "old")
        c.put("k", "new")
        assert c.get("k") == "new"

    def test_clear(self):
        c = self._make()
        c.put("a", 1)
        c.put("b", 2)
        cleared = c.clear()
        assert cleared == 2
        assert c.get("a") is None
        assert c.get("b") is None

    def test_stats(self):
        c = self._make()
        c.put("a", 1)
        c.get("a")       # hit
        c.get("miss")    # miss
        s = c.stats()
        assert s["name"] == "test"
        assert s["entries"] == 1
        assert s["hits"] == 1
        assert s["misses"] == 1
        assert "50.0%" in s["hit_rate"]

    def test_stats_no_requests(self):
        c = self._make()
        s = c.stats()
        assert s["hit_rate"] == "N/A"

    def test_size_bytes_tracking(self):
        c = self._make(max_bytes=100)
        c.put("a", b"x" * 50, size_bytes=50)
        c.put("b", b"y" * 50, size_bytes=50)
        assert c.stats()["total_bytes"] == 100
        # Next put should evict to make room
        c.put("c", b"z" * 60, size_bytes=60)
        assert c.stats()["total_bytes"] <= 110


# ═══════════════════════════════════════════════════════════════════
# Public helper layer tests
# ═══════════════════════════════════════════════════════════════════

class TestDownloadCacheHelpers:

    def test_put_and_get(self):
        put_cached_download("http://test.com/file.pdf", b"pdf-bytes")
        result = get_cached_download("http://test.com/file.pdf")
        assert result == b"pdf-bytes"

    def test_miss(self):
        assert get_cached_download("http://no-such-url.com/x") is None


class TestDocumentCacheHelpers:

    def test_put_and_get(self):
        put_cached_document("hash123", {"content": "hello"})
        assert get_cached_document("hash123") == {"content": "hello"}

    def test_miss(self):
        assert get_cached_document("nonexistent-hash") is None


class TestCacheStats:

    def test_returns_all_layers(self):
        stats = cache_stats()
        assert "download" in stats
        assert "document" in stats
        assert "retriever" in stats
        assert "faiss_disk" in stats

    def test_each_layer_has_name(self):
        stats = cache_stats()
        assert stats["download"]["name"] == "download"
        assert stats["document"]["name"] == "document"
        assert stats["retriever"]["name"] == "retriever"
        assert stats["faiss_disk"]["name"] == "faiss_disk"


class TestClearAll:

    def test_returns_counts(self):
        result = clear_all()
        assert "download_cleared" in result
        assert "document_cleared" in result
        assert "retriever_cleared" in result
        assert "faiss_disk_cleared" in result
        # All values should be ints ≥ 0
        for v in result.values():
            assert isinstance(v, int) and v >= 0
