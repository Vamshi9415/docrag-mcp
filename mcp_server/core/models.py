"""Lazy-loaded ML model singletons — embeddings & reranker only.

This server is a pure deterministic tool bridge.  It does NOT load any
LLM.  Only embedding models (for FAISS vector search) and the
cross-encoder reranker are managed here.

Models are initialised on first access (not at import time) so that
server startup is fast.  Thread-safety is guaranteed by a double-checked
lock.
"""

from __future__ import annotations

import threading
from typing import Any

from mcp_server.core.config import DEVICE, RERANK_AVAILABLE, model_config
from mcp_server.core.errors import ModelLoadError
from mcp_server.core.logging import get_logger

logger = get_logger("models")

# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_embeddings_fast: Any = None
_embeddings_accurate: Any = None
_reranker: Any = None
_loaded = False


def _ensure_models_loaded() -> None:
    """Load embedding + reranker models (called once, lazily)."""
    global _embeddings_fast, _embeddings_accurate, _reranker, _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        try:
            logger.info("models.loading_start — loading all ML models")

            from langchain_huggingface import HuggingFaceEmbeddings

            logger.info(f"models.loading — embedding_fast: {model_config.embedding_fast} (device={DEVICE})")
            _embeddings_fast = HuggingFaceEmbeddings(
                model_name=model_config.embedding_fast,
                model_kwargs={"device": DEVICE},
                encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
            )
            logger.info(f"models.loaded — embedding_fast: {model_config.embedding_fast} ✓")

            logger.info(f"models.loading — embedding_accurate: {model_config.embedding_accurate} (device={DEVICE})")
            _embeddings_accurate = HuggingFaceEmbeddings(
                model_name=model_config.embedding_accurate,
                model_kwargs={"device": DEVICE},
                encode_kwargs={"normalize_embeddings": True, "batch_size": 32},
            )
            logger.info(f"models.loaded — embedding_accurate: {model_config.embedding_accurate} ✓")

            if RERANK_AVAILABLE:
                from sentence_transformers import CrossEncoder

                logger.info(f"models.loading — reranker: {model_config.reranker}")
                _reranker = CrossEncoder(model_config.reranker, max_length=512)
                logger.info(f"models.loaded — reranker: {model_config.reranker} ✓")
            else:
                logger.info("models.skip — reranker not available (sentence_transformers not installed)")

            _loaded = True
            logger.info("models.loading_complete — all models ready")
        except Exception as exc:
            logger.exception("models.loading_failed")
            raise ModelLoadError(f"Failed to initialise models: {exc}") from exc


# ---------------------------------------------------------------------------
# Public getters
# ---------------------------------------------------------------------------
def get_embeddings_fast():
    _ensure_models_loaded()
    return _embeddings_fast


def get_embeddings_accurate():
    _ensure_models_loaded()
    return _embeddings_accurate


def get_reranker():
    _ensure_models_loaded()
    return _reranker


def models_loaded() -> bool:
    """Return ``True`` if the embedding models are already in memory."""
    return _loaded
