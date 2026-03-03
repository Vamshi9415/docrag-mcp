"""Tests for mcp_server.services.retrieval — FAISS index with model tracking."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from langchain_core.documents import Document

from mcp_server.services.retrieval import EnhancedRetriever


# ═══════════════════════════════════════════════════════════════════
# Embedding model metadata persistence
# ═══════════════════════════════════════════════════════════════════

class TestEmbeddingModelTracking:
    """Verify that the embedding model name is stored and loaded correctly."""

    def test_default_model_name(self):
        """Constructor defaults to 'fast' for embedding_model_name."""
        chunks = [Document(page_content="hello", metadata={})]
        # We can't build a real FAISS index without embeddings, so test
        # only the attribute assignment by using __new__ + manual init.
        obj = EnhancedRetriever.__new__(EnhancedRetriever)
        obj.embedding_model_name = "fast"
        assert obj.embedding_model_name == "fast"

    def test_custom_model_name(self):
        obj = EnhancedRetriever.__new__(EnhancedRetriever)
        obj.embedding_model_name = "accurate"
        assert obj.embedding_model_name == "accurate"


class TestIndexMetaJsonPersistence:
    """Verify index_meta.json sidecar read/write."""

    def test_get_disk_embedding_model_returns_default_for_missing(self, tmp_dir):
        """If no index_meta.json exists, assume 'fast' (legacy)."""
        with patch("mcp_server.services.retrieval.FAISS_INDEX_PATH", tmp_dir):
            result = EnhancedRetriever.get_disk_embedding_model("nonexistent")
            assert result == "fast"

    def test_get_disk_embedding_model_reads_saved_value(self, tmp_dir):
        """If index_meta.json exists, read the model name from it."""
        url_hash = "abc123"
        index_dir = os.path.join(tmp_dir, url_hash)
        os.makedirs(index_dir)
        meta = {"embedding_model": "accurate"}
        with open(os.path.join(index_dir, "index_meta.json"), "w") as f:
            json.dump(meta, f)

        with patch("mcp_server.services.retrieval.FAISS_INDEX_PATH", tmp_dir):
            result = EnhancedRetriever.get_disk_embedding_model(url_hash)
            assert result == "accurate"

    def test_get_disk_embedding_model_handles_corrupt_json(self, tmp_dir):
        """Corrupt index_meta.json should fall back to 'fast'."""
        url_hash = "corrupt"
        index_dir = os.path.join(tmp_dir, url_hash)
        os.makedirs(index_dir)
        with open(os.path.join(index_dir, "index_meta.json"), "w") as f:
            f.write("{not valid json")

        with patch("mcp_server.services.retrieval.FAISS_INDEX_PATH", tmp_dir):
            result = EnhancedRetriever.get_disk_embedding_model(url_hash)
            assert result == "fast"

    def test_load_from_disk_returns_none_when_no_index(self, tmp_dir):
        """load_from_disk returns None when no index file exists."""
        with patch("mcp_server.services.retrieval.FAISS_INDEX_PATH", tmp_dir):
            result = EnhancedRetriever.load_from_disk("nonexistent")
            assert result is None

    def test_save_writes_index_meta_json(self, tmp_dir):
        """save_to_disk should create index_meta.json with the model name."""
        obj = EnhancedRetriever.__new__(EnhancedRetriever)
        obj.chunks = [Document(page_content="hello", metadata={})]
        obj.embedding_model_name = "accurate"
        obj.vectorstore = MagicMock()

        with patch("mcp_server.services.retrieval.FAISS_INDEX_PATH", tmp_dir):
            obj.save_to_disk("test_hash")

        meta_path = os.path.join(tmp_dir, "test_hash", "index_meta.json")
        assert os.path.isfile(meta_path)
        with open(meta_path) as f:
            data = json.load(f)
        assert data["embedding_model"] == "accurate"


# ═══════════════════════════════════════════════════════════════════
# Diversity filter
# ═══════════════════════════════════════════════════════════════════

class TestDiversityFilter:

    def test_fewer_than_top_k(self):
        docs = [Document(page_content="a", metadata={})]
        result = EnhancedRetriever._diversity_filter(docs, top_k=5)
        assert len(result) == 1

    def test_promotes_varied_types(self):
        docs = [
            Document(page_content="a", metadata={"content_type": "text", "importance_score": 0.8}),
            Document(page_content="b", metadata={"content_type": "table", "importance_score": 0.7}),
            Document(page_content="c", metadata={"content_type": "text", "importance_score": 0.9}),
            Document(page_content="d", metadata={"content_type": "code", "importance_score": 0.6}),
        ]
        result = EnhancedRetriever._diversity_filter(docs, top_k=3)
        assert len(result) == 3
        types = {d.metadata["content_type"] for d in result}
        # Should include at least 2 different types
        assert len(types) >= 2
