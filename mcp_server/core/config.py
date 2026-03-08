"""Centralised configuration, feature flags, and path constants.

All tuneable knobs live here so the rest of the package can import a single
frozen dataclass instance instead of scattering ``os.getenv`` calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_FILES_PATH = os.path.join(BASE_DIR, "temp_files")
REQUEST_LOGS_PATH = os.path.join(BASE_DIR, "request_logs")
FAISS_INDEX_PATH = os.path.join(BASE_DIR, "faiss_indexes")
os.makedirs(TEMP_FILES_PATH, exist_ok=True)
os.makedirs(REQUEST_LOGS_PATH, exist_ok=True)
os.makedirs(FAISS_INDEX_PATH, exist_ok=True)

# ---------------------------------------------------------------------------
# Device detection
# ---------------------------------------------------------------------------
try:
    import torch

    if torch.cuda.is_available():
        DEVICE = "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        DEVICE = "mps"
    else:
        DEVICE = "cpu"
except ImportError:
    DEVICE = "cpu"

# ---------------------------------------------------------------------------
# Feature flags (graceful degradation when optional deps are missing)
# ---------------------------------------------------------------------------
RERANK_AVAILABLE = True
try:
    from sentence_transformers import CrossEncoder  # noqa: F401
except ImportError:
    RERANK_AVAILABLE = False

OCR_AVAILABLE = True
try:
    import pytesseract  # noqa: F401
except ImportError:
    OCR_AVAILABLE = False

LANG_DETECT_AVAILABLE = True
try:
    from langdetect import detect  # noqa: F401
except ImportError:
    LANG_DETECT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ServerConfig:
    """Server identity and network settings."""
    name: str = "RAG Document Server"
    version: str = "2.0.0"
    host: str = "127.0.0.1"
    port: int = 8000
    transport: str = "streamable-http"


@dataclass(frozen=True)
class ModelConfig:
    """Hugging Face model identifiers (embeddings + reranker only — no LLM)."""
    embedding_fast: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_accurate: str = "BAAI/bge-small-en-v1.5"
    reranker: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@dataclass(frozen=True)
class CacheConfig:
    """TTL cache sizing."""
    default_ttl: int = 1800  # 30 minutes
    max_download_entries: int = 50
    max_document_entries: int = 50
    max_retriever_entries: int = 20
    max_download_bytes: int = 500 * 1024 * 1024  # 500 MB


@dataclass(frozen=True)
class SecurityConfig:
    """Rate-limiting and input-size guards."""
    rate_limit_rpm: int = int(os.getenv("MCP_RATE_LIMIT_RPM", "60"))
    max_url_length: int = 2048
    max_text_length: int = 100_000
    request_timeout: int = int(os.getenv("MCP_REQUEST_TIMEOUT", "300"))


# ---------------------------------------------------------------------------
# Singleton instances (import these, not the classes)
# ---------------------------------------------------------------------------
server_config = ServerConfig()
model_config = ModelConfig()
cache_config = CacheConfig()
security_config = SecurityConfig()
