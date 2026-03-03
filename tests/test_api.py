"""Tests for mcp_server.api — FastAPI REST endpoints via TestClient."""

from __future__ import annotations

import io
import os
import pytest

from fastapi.testclient import TestClient

from mcp_server.api import app
from mcp_server.core.config import security_config

# Use the actual loaded API key (set in .env at import time), not a hardcoded one
API_KEY = security_config.api_key
HEADERS = {"x-api-key": API_KEY}


@pytest.fixture()
def client():
    """Synchronous TestClient (wraps the ASGI app in a thread)."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ═══════════════════════════════════════════════════════════════════
# Auth middleware
# ═══════════════════════════════════════════════════════════════════

class TestAuthMiddleware:

    def test_no_key_rejected(self, client):
        r = client.get("/api/health")
        # /api/health is a normal FastAPI route, NOT the ASGI /health.
        # It goes through the guard_middleware which checks auth.
        assert r.status_code == 401

    def test_wrong_key_rejected(self, client):
        r = client.get("/api/health", headers={"x-api-key": "wrong"})
        assert r.status_code == 401

    def test_correct_key_accepted(self, client):
        r = client.get("/api/health", headers=HEADERS)
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# GET /api/health
# ═══════════════════════════════════════════════════════════════════

class TestHealthEndpoint:

    def test_status_healthy(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert data["status"] == "healthy"

    def test_contains_version(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert "version" in data

    def test_features_present(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert "features" in data
        feats = data["features"]
        assert "adaptive_chunking" in feats
        assert "vector_retrieval" in feats
        assert "reranking" in feats

    def test_security_info(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert "security" in data
        assert data["security"]["auth_enabled"] is True

    def test_models_loaded(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert "models_loaded" in data

    def test_cache_stats(self, client):
        r = client.get("/api/health", headers=HEADERS)
        data = r.json()
        assert "cache" in data
        assert "download" in data["cache"]

    def test_has_request_id_header(self, client):
        r = client.get("/api/health", headers=HEADERS)
        assert "x-request-id" in r.headers


# ═══════════════════════════════════════════════════════════════════
# GET /  (root listing)
# ═══════════════════════════════════════════════════════════════════

class TestRootEndpoint:

    def test_root_lists_endpoints(self, client):
        r = client.get("/", headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert "endpoints" in data
        assert "document" in data["endpoints"]
        assert "extract" in data["endpoints"]
        assert "utility" in data["endpoints"]

    def test_root_has_server_name(self, client):
        r = client.get("/", headers=HEADERS)
        data = r.json()
        assert "server" in data


# ═══════════════════════════════════════════════════════════════════
# POST /api/cache
# ═══════════════════════════════════════════════════════════════════

class TestCacheEndpoint:

    def test_stats_action(self, client):
        r = client.post("/api/cache", json={"action": "stats"}, headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "stats"
        assert "download" in data

    def test_clear_action(self, client):
        r = client.post("/api/cache", json={"action": "clear"}, headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "clear"
        assert "download_cleared" in data

    def test_default_action_is_stats(self, client):
        r = client.post("/api/cache", json={}, headers=HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["action"] == "stats"


# ═══════════════════════════════════════════════════════════════════
# POST /api/detect-language
# ═══════════════════════════════════════════════════════════════════

class TestDetectLanguageEndpoint:

    def test_english(self, client):
        r = client.post(
            "/api/detect-language",
            json={"text": "This is a perfectly normal English sentence for testing."},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert "language_code" in data
        assert "language_name" in data

    def test_missing_text_rejected(self, client):
        r = client.post("/api/detect-language", json={}, headers=HEADERS)
        assert r.status_code == 422  # Pydantic validation error


# ═══════════════════════════════════════════════════════════════════
# POST /api/upload
# ═══════════════════════════════════════════════════════════════════

class TestUploadEndpoint:

    def test_upload_csv(self, client, sample_csv_bytes):
        r = client.post(
            "/api/upload",
            files={"file": ("test_data.csv", io.BytesIO(sample_csv_bytes), "text/csv")},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "test_data.csv"
        assert data["size_bytes"] == len(sample_csv_bytes)
        assert "document_url" in data
        assert "/uploads/" in data["document_url"]
        assert "saved_as" in data

    def test_upload_txt(self, client):
        content = b"Hello World from test file."
        r = client.post(
            "/api/upload",
            files={"file": ("readme.txt", io.BytesIO(content), "text/plain")},
            headers=HEADERS,
        )
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "readme.txt"
        assert "document_url" in data

    def test_upload_unsupported_extension(self, client):
        r = client.post(
            "/api/upload",
            files={"file": ("malware.exe", io.BytesIO(b"MZ..."), "application/octet-stream")},
            headers=HEADERS,
        )
        assert r.status_code == 400
        assert "Unsupported" in r.json()["detail"]

    def test_upload_no_file(self, client):
        r = client.post("/api/upload", headers=HEADERS)
        assert r.status_code == 422  # missing required 'file'

    def test_upload_too_large(self, client):
        """Files > 50 MB should be rejected."""
        big = b"x" * (51 * 1024 * 1024)
        r = client.post(
            "/api/upload",
            files={"file": ("huge.txt", io.BytesIO(big), "text/plain")},
            headers=HEADERS,
        )
        assert r.status_code == 413

    def test_uploaded_file_is_accessible(self, client, sample_csv_bytes):
        """After uploading, the file should be retrievable at its URL."""
        r = client.post(
            "/api/upload",
            files={"file": ("accessible.csv", io.BytesIO(sample_csv_bytes), "text/csv")},
            headers=HEADERS,
        )
        data = r.json()
        # Fetch the uploaded file via the static mount (no auth needed for static)
        saved_as = data["saved_as"]
        r2 = client.get(f"/uploads/{saved_as}", headers=HEADERS)
        assert r2.status_code == 200
        assert r2.content == sample_csv_bytes


# ═══════════════════════════════════════════════════════════════════
# Input validation on document endpoints
# ═══════════════════════════════════════════════════════════════════

class TestInputValidation:

    def test_process_document_invalid_url(self, client):
        r = client.post(
            "/api/process-document",
            json={"document_url": "not-a-url"},
            headers=HEADERS,
        )
        assert r.status_code == 400

    def test_chunk_document_invalid_url(self, client):
        r = client.post(
            "/api/chunk-document",
            json={"document_url": "ftp://bad"},
            headers=HEADERS,
        )
        assert r.status_code == 400

    def test_retrieve_chunks_missing_query(self, client):
        r = client.post(
            "/api/retrieve-chunks",
            json={"document_url": "https://example.com/file.pdf"},
            headers=HEADERS,
        )
        assert r.status_code == 422  # missing required 'query'

    def test_retrieve_chunks_invalid_url(self, client):
        r = client.post(
            "/api/retrieve-chunks",
            json={"document_url": "bad", "query": "hello"},
            headers=HEADERS,
        )
        assert r.status_code == 400

    def test_extract_pdf_invalid_url(self, client):
        r = client.post(
            "/api/extract/pdf",
            json={"document_url": "xyz"},
            headers=HEADERS,
        )
        assert r.status_code == 400
