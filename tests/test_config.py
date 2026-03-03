"""Tests for mcp_server.core.config — dataclasses, paths, feature flags."""

from __future__ import annotations

import os


class TestPaths:
    """Verify that config creates required directories and exports paths."""

    def test_temp_files_path_exists(self):
        from mcp_server.core.config import TEMP_FILES_PATH
        assert os.path.isdir(TEMP_FILES_PATH)

    def test_request_logs_path_exists(self):
        from mcp_server.core.config import REQUEST_LOGS_PATH
        assert os.path.isdir(REQUEST_LOGS_PATH)

    def test_faiss_index_path_exists(self):
        from mcp_server.core.config import FAISS_INDEX_PATH
        assert os.path.isdir(FAISS_INDEX_PATH)

    def test_base_dir_is_package_dir(self):
        from mcp_server.core.config import BASE_DIR
        # BASE_DIR is the parent of core/ — i.e. the mcp_server package dir
        assert os.path.isfile(os.path.join(BASE_DIR, "__init__.py"))


class TestServerConfig:
    """ServerConfig frozen dataclass defaults."""

    def test_defaults(self):
        from mcp_server.core.config import server_config
        assert server_config.name == "RAG Document Server"
        assert server_config.version == "2.0.0"
        assert server_config.host == "127.0.0.1"
        assert server_config.port == 8000
        assert server_config.transport == "streamable-http"

    def test_frozen(self):
        from mcp_server.core.config import server_config
        import dataclasses
        with pytest.raises(dataclasses.FrozenInstanceError):
            server_config.version = "999"


class TestModelConfig:

    def test_model_ids(self):
        from mcp_server.core.config import model_config
        assert "MiniLM" in model_config.embedding_fast
        assert "bge" in model_config.embedding_accurate
        assert "marco" in model_config.reranker


class TestCacheConfig:

    def test_ttl_value(self):
        from mcp_server.core.config import cache_config
        assert cache_config.default_ttl == 1800

    def test_max_entries_positive(self):
        from mcp_server.core.config import cache_config
        assert cache_config.max_download_entries > 0
        assert cache_config.max_document_entries > 0
        assert cache_config.max_retriever_entries > 0


class TestSecurityConfig:

    def test_auth_enabled_when_key_set(self):
        """The conftest fixture sets MCP_API_KEY so auth should be enabled."""
        from mcp_server.core.config import SecurityConfig
        cfg = SecurityConfig()
        assert cfg.auth_enabled is True
        assert cfg.api_key != ""

    def test_rate_limit_positive(self):
        from mcp_server.core.config import security_config
        assert security_config.rate_limit_rpm > 0


class TestFeatureFlags:

    def test_device_valid(self):
        from mcp_server.core.config import DEVICE
        assert DEVICE in ("cpu", "cuda", "mps")

    def test_rerank_flag_is_bool(self):
        from mcp_server.core.config import RERANK_AVAILABLE
        assert isinstance(RERANK_AVAILABLE, bool)

    def test_ocr_flag_is_bool(self):
        from mcp_server.core.config import OCR_AVAILABLE
        assert isinstance(OCR_AVAILABLE, bool)

    def test_lang_detect_flag_is_bool(self):
        from mcp_server.core.config import LANG_DETECT_AVAILABLE
        assert isinstance(LANG_DETECT_AVAILABLE, bool)


# Need pytest for the raises check
import pytest
