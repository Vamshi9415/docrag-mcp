# MCP Server — Test Suite

This folder contains **two types** of tests:

| Type | Framework | Files | What it covers |
|------|-----------|-------|---------|
| **Python unit + integration** | `pytest` | `test_*.py` | Config, errors, guards, cache, chunking, concurrency, downloader |

---

## Python Tests (pytest)

### Prerequisites

```bash
# From project root (one-time setup)
pip install -r requirements.txt
pip install pytest pytest-asyncio httpx
```

> **Note:** `httpx` is already in `requirements.txt` but `pytest` and `pytest-asyncio` are test-only dependencies.

### Run all tests

```bash
# From the project root
pytest
```

This will discover and run every `tests/test_*.py` file.

### Run with verbose output

```bash
pytest -v
```

### Run a specific test file

```bash
pytest tests/test_config.py
pytest tests/test_chunking.py
```

### Run a single test class or function

```bash
pytest tests/test_guards.py::TestValidateUrl
pytest tests/test_cache.py::TestTTLCache::test_put_and_get
```

### Run only async tests

```bash
pytest -m asyncio
```

### Skip slow network tests

```bash
pytest -k "not real_url"
```

---

## Test Files

| File | Layer | Tests | Key coverage |
|------|-------|-------|-------------|
| `conftest.py` | Fixtures | — | `tmp_dir`, `sample_text`, `sample_csv_bytes` |
| `test_config.py` | `core/config.py` | 14 | Paths, ServerConfig, ModelConfig, CacheConfig, SecurityConfig, feature flags |
| `test_errors.py` | `core/errors.py` | 12 | Exception hierarchy, codes, inheritance chain, parametrised |
| `test_guards.py` | `middleware/guards.py` | 15 | Token bucket, rate limiting, URL validation, text validation |
| `test_cache.py` | `services/cache.py` | 16 | TTL cache CRUD, expiry, eviction, size tracking, stats, public helpers, clear_all |
| `test_chunking.py` | `services/chunking.py` | 16 | Adaptive params, scaling, chunk creation, metadata, importance score, content type |
| `test_concurrency.py` | `core/concurrency.py` | 7 | GPU semaphore, `run_in_gpu_pool`, coalesced build, cleanup |
| `test_downloader.py` | `services/downloader.py` | 5 | httpx client singleton, cache hit, invalid URL, real download (optional) |

**Total: ~109 test cases**

---

## Configuration

All pytest settings live in [`pytest.ini`](../pytest.ini) at the project root:

```ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = -v --tb=short
```

The `conftest.py` provides shared fixtures (`sample_text`, `tmp_dir`, `sample_csv_bytes`) for all tests.

---

## Adding New Tests

1. Create `tests/test_<module>.py`
2. Import the module under test
3. Use fixtures from `conftest.py` (e.g., `sample_text`, `tmp_dir`)
4. For async tests, mark with `@pytest.mark.asyncio`
5. For API tests, use the `client` fixture (FastAPI `TestClient`)

```python
# Example
import pytest

class TestMyFeature:
    def test_something(self, sample_text):
        assert len(sample_text) > 0

    @pytest.mark.asyncio
    async def test_async_thing(self):
        result = await some_async_function()
        assert result is not None
```

## Request Flow

```
Client Request
      │
      ▼
MCPRouter (guards.py)
      │
┌─────┴──────┐
GET /health    GET /info
(200, probe)  (200, caps)
      │
everything else
      │
      ▼
FastMCP streamable_http_app()
    POST /mcp only
      │
      ▼
Tool Execution
```

`MCPRouter` is a pure ASGI class in `guards.py` that adds `/health` and `/info`
endpoints before forwarding all other requests to FastMCP.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `[ERROR] Server not reachable` | Server not started | Run `python -m mcp_server --transport streamable-http` |
| All tests return connection error | Port conflict | Change port: `python -m mcp_server --port 9000` |
