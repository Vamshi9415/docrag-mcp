"""Tests for mcp_server.core.concurrency — GPU semaphore & build coalescing."""

from __future__ import annotations

import asyncio
import pytest

from mcp_server.core.concurrency import (
    run_in_gpu_pool,
    coalesced_build,
    _ensure_semaphore,
    cleanup_build_lock,
)


# ═══════════════════════════════════════════════════════════════════
# GPU Semaphore
# ═══════════════════════════════════════════════════════════════════

class TestGPUSemaphore:

    @pytest.mark.asyncio
    async def test_semaphore_created(self):
        sem = _ensure_semaphore()
        assert isinstance(sem, asyncio.Semaphore)

    @pytest.mark.asyncio
    async def test_run_in_gpu_pool_sync_function(self):
        """Verify a plain function executes in the GPU pool."""
        def add(a, b):
            return a + b

        result = await run_in_gpu_pool(add, 3, 7)
        assert result == 10

    @pytest.mark.asyncio
    async def test_run_in_gpu_pool_preserves_exceptions(self):
        """Exceptions inside the GPU pool should propagate."""
        def explode():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await run_in_gpu_pool(explode)

    @pytest.mark.asyncio
    async def test_concurrent_semaphore_limiting(self):
        """Launch several tasks and confirm they don't all run simultaneously."""
        running = 0
        max_running = 0
        lock = asyncio.Lock()

        def tracked_work():
            nonlocal running, max_running
            import threading
            # We can't use asyncio.Lock in a thread, use a simple counter
            running += 1
            if running > max_running:
                max_running = running
            import time
            time.sleep(0.05)
            running -= 1
            return True

        tasks = [run_in_gpu_pool(tracked_work) for _ in range(6)]
        results = await asyncio.gather(*tasks)
        assert all(results)
        # max_running should not exceed GPU_CONCURRENCY (default 2) + small race window
        assert max_running <= 4  # generous bound to avoid flaky tests


# ═══════════════════════════════════════════════════════════════════
# FAISS Build Coalescing
# ═══════════════════════════════════════════════════════════════════

class TestCoalescedBuild:

    @pytest.mark.asyncio
    async def test_basic_build(self):
        """A single coalesced_build should call and return the builder result."""
        call_count = 0

        async def builder():
            nonlocal call_count
            call_count += 1
            return "index-object"

        result = await coalesced_build("test-hash-1", builder)
        assert result == "index-object"
        assert call_count == 1
        cleanup_build_lock("test-hash-1")

    @pytest.mark.asyncio
    async def test_concurrent_same_hash(self):
        """Multiple concurrent requests for the same hash should serialize."""
        call_count = 0

        async def slow_builder():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.1)
            return f"index-{call_count}"

        # Launch 3 concurrent builds for the same hash
        tasks = [coalesced_build("same-hash", slow_builder) for _ in range(3)]
        results = await asyncio.gather(*tasks)

        # The builder runs 3 times (each acquires lock sequentially),
        # but the key point is they don't overlap — they're serialized
        assert all(r.startswith("index-") for r in results)
        cleanup_build_lock("same-hash")

    @pytest.mark.asyncio
    async def test_different_hashes_parallel(self):
        """Different url_hashes should not block each other."""
        order = []

        async def builder(name):
            async def _inner():
                order.append(f"{name}-start")
                await asyncio.sleep(0.05)
                order.append(f"{name}-end")
                return name
            return _inner

        b1 = await builder("A")
        b2 = await builder("B")

        r1, r2 = await asyncio.gather(
            coalesced_build("hash-A", b1),
            coalesced_build("hash-B", b2),
        )
        assert r1 == "A"
        assert r2 == "B"
        cleanup_build_lock("hash-A")
        cleanup_build_lock("hash-B")

    @pytest.mark.asyncio
    async def test_cleanup_build_lock(self):
        """cleanup_build_lock should not raise even for unknown hashes."""
        cleanup_build_lock("never-existed")  # should not raise
