"""Concurrency primitives — GPU semaphore and FAISS build coalescing.

Solves two production problems:

1. **GPU / embedding semaphore** — Embedding models and FAISS use significant
   GPU/CPU memory.  Without a semaphore, ``N`` concurrent requests each build
   a FAISS index simultaneously, causing OOM or extreme slowdown.  The
   ``gpu_semaphore`` limits how many heavy compute operations run at once.

2. **FAISS build coalescing** — When 10 requests arrive for the *same* URL at
   the same time, only the *first* should build the FAISS index.  The other 9
   wait on an ``asyncio.Lock`` keyed by ``url_hash``, then read from cache.
   This avoids 10× redundant embedding work.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar, Callable, Any

from mcp_server.core.logging import get_logger

logger = get_logger("concurrency")

T = TypeVar("T")

# ═══════════════════════════════════════════════════════════════════
# GPU / Embedding Semaphore
# ═══════════════════════════════════════════════════════════════════

# Max concurrent heavy compute ops (FAISS build, retrieval, embedding).
# Default 2: allows parallelism while preventing OOM on modest hardware.
# Override via GPU_CONCURRENCY env var.
import os

_GPU_CONCURRENCY = int(os.getenv("GPU_CONCURRENCY", "2"))

gpu_semaphore: asyncio.Semaphore | None = None
"""Lazily initialised — must be created inside a running event loop."""

# Dedicated thread pool for CPU/GPU-bound work (FAISS, embeddings).
# Separate from the default executor so we don't starve I/O tasks.
_gpu_pool = ThreadPoolExecutor(
    max_workers=_GPU_CONCURRENCY + 1,
    thread_name_prefix="gpu-pool",
)


def _ensure_semaphore() -> asyncio.Semaphore:
    """Return the GPU semaphore, creating it if needed."""
    global gpu_semaphore
    if gpu_semaphore is None:
        gpu_semaphore = asyncio.Semaphore(_GPU_CONCURRENCY)
        logger.info(
            "concurrency.gpu_semaphore.init",
            extra={"detail": f"max_concurrent={_GPU_CONCURRENCY}"},
        )
    return gpu_semaphore


async def run_in_gpu_pool(fn: Callable[..., T], *args: Any) -> T:
    """Run *fn* in the GPU thread-pool, guarded by the GPU semaphore.

    Only ``_GPU_CONCURRENCY`` (default 2) heavy operations can execute at
    the same time.  Other callers ``await`` on the semaphore — they don't
    block the event loop.
    """
    sem = _ensure_semaphore()
    loop = asyncio.get_running_loop()
    async with sem:
        return await loop.run_in_executor(_gpu_pool, fn, *args)


# ═══════════════════════════════════════════════════════════════════
# FAISS Build Coalescing (per-URL lock)
# ═══════════════════════════════════════════════════════════════════

_build_locks: dict[str, asyncio.Lock] = {}
_build_locks_guard = asyncio.Lock() if False else None  # created lazily


async def _get_build_lock(url_hash: str) -> asyncio.Lock:
    """Return (or create) an ``asyncio.Lock`` for *url_hash*.

    The first request for a given URL acquires the lock and builds the
    FAISS index.  Concurrent requests for the **same** URL wait here,
    then read from cache.  Different URLs are fully parallel (modulo the
    GPU semaphore).
    """
    global _build_locks_guard
    if _build_locks_guard is None:
        _build_locks_guard = asyncio.Lock()

    async with _build_locks_guard:
        if url_hash not in _build_locks:
            _build_locks[url_hash] = asyncio.Lock()
        return _build_locks[url_hash]


async def coalesced_build(url_hash: str, build_fn):
    """Acquire the per-URL lock, then call *build_fn*.

    ``build_fn`` is an ``async`` callable.  If another coroutine is
    already building for the same ``url_hash``, we wait and skip the
    build (the caller should re-check cache after ``coalesced_build``
    returns).

    Returns whatever *build_fn* returns (typically an ``EnhancedRetriever``).
    """
    lock = await _get_build_lock(url_hash)
    async with lock:
        return await build_fn()


def cleanup_build_lock(url_hash: str) -> None:
    """Remove a build lock after it's no longer needed (optional)."""
    _build_locks.pop(url_hash, None)
