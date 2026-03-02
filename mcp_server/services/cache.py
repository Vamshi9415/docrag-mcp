"""Three-layer TTL cache: download → document → retriever.

Each layer is an independent ``_TTLCache`` instance with configurable
max-entry and TTL limits.  Public helpers are thin wrappers so the rest
of the code-base never touches the internals.
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from mcp_server.core.config import cache_config
from mcp_server.core.logging import get_logger

logger = get_logger("cache")


# ---------------------------------------------------------------------------
# Generic TTL cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    value: Any
    expires_at: float
    size_bytes: int = 0


class _TTLCache:
    """Thread-safe, size-bounded TTL cache."""

    def __init__(self, max_entries: int, ttl: int, name: str, max_bytes: int = 0):
        self.max_entries = max_entries
        self.ttl = ttl
        self.name = name
        self.max_bytes = max_bytes
        self._store: Dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0

    # ── Core operations ───────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.time() > entry.expires_at:
                self._evict_key(key)
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def put(self, key: str, value: Any, size_bytes: int = 0) -> None:
        with self._lock:
            self._evict_expired()
            if key in self._store:
                self._evict_key(key)
            if self.max_bytes and self._total_bytes + size_bytes > self.max_bytes:
                self._evict_oldest()
            while len(self._store) >= self.max_entries:
                self._evict_oldest()
            self._store[key] = _CacheEntry(
                value=value,
                expires_at=time.time() + self.ttl,
                size_bytes=size_bytes,
            )
            self._total_bytes += size_bytes

    def clear(self) -> int:
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._total_bytes = 0
            return count

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "entries": len(self._store),
                "max_entries": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (
                    f"{self._hits / (self._hits + self._misses) * 100:.1f}%"
                    if (self._hits + self._misses)
                    else "N/A"
                ),
                "total_bytes": self._total_bytes,
            }

    # ── Internal helpers ──────────────────────────────────────────

    def _evict_key(self, key: str) -> None:
        entry = self._store.pop(key, None)
        if entry:
            self._total_bytes -= entry.size_bytes

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, v in self._store.items() if now > v.expires_at]
        for k in expired:
            self._evict_key(k)

    def _evict_oldest(self) -> None:
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
        self._evict_key(oldest_key)


# ---------------------------------------------------------------------------
# Layer singletons
# ---------------------------------------------------------------------------

download_cache = _TTLCache(
    max_entries=cache_config.max_download_entries,
    ttl=cache_config.default_ttl,
    name="download",
    max_bytes=cache_config.max_download_bytes,
)

document_cache = _TTLCache(
    max_entries=cache_config.max_document_entries,
    ttl=cache_config.default_ttl,
    name="document",
)

retriever_cache = _TTLCache(
    max_entries=cache_config.max_retriever_entries,
    ttl=cache_config.default_ttl,
    name="retriever",
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_cached_download(url: str) -> Optional[bytes]:
    return download_cache.get(url)


def put_cached_download(url: str, data: bytes) -> None:
    download_cache.put(url, data, size_bytes=len(data))


def get_cached_document(key: str):
    return document_cache.get(key)


def put_cached_document(key: str, doc) -> None:
    document_cache.put(key, doc)


def get_cached_retriever(key: str):
    return retriever_cache.get(key)


def put_cached_retriever(key: str, retriever) -> None:
    retriever_cache.put(key, retriever)


def clear_all() -> Dict[str, int]:
    """Flush every cache layer and return eviction counts."""
    result = {
        "download_cleared": download_cache.clear(),
        "document_cleared": document_cache.clear(),
        "retriever_cleared": retriever_cache.clear(),
    }
    logger.info("cache.clear_all", extra=result)
    return result


def cache_stats() -> Dict[str, Any]:
    return {
        "download": download_cache.stats(),
        "document": document_cache.stats(),
        "retriever": retriever_cache.stats(),
    }
