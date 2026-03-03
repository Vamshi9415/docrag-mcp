"""Enhanced retriever — FAISS vector search with cross-encoder reranking.

Builds a FAISS index on the fly from chunked documents and, when the
``cross-encoder`` model is available, reranks the top-K results for
higher precision.  An importance-based diversity filter promotes chunks
with varied content types.

FAISS indexes are persisted to disk (``faiss_indexes/<url_hash>/``) so
they survive server restarts and can be shared across worker processes.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

from mcp_server.core.config import RERANK_AVAILABLE, FAISS_INDEX_PATH
from mcp_server.core.models import get_reranker, get_embeddings_fast, get_embeddings_accurate
from mcp_server.core.logging import get_logger

logger = get_logger("retrieval")


class EnhancedRetriever:
    """Build a FAISS index and retrieve the best chunks for a query."""

    def __init__(self, embeddings, chunks: List[Document], embedding_model_name: str = "fast"):
        self.vectorstore = FAISS.from_documents(chunks, embeddings)
        self.chunks = chunks
        self._embeddings = embeddings
        self.embedding_model_name = embedding_model_name

    # ------------------------------------------------------------------
    # Disk persistence
    # ------------------------------------------------------------------

    def save_to_disk(self, url_hash: str) -> None:
        """Persist the FAISS index + chunk metadata to disk."""
        index_dir = os.path.join(FAISS_INDEX_PATH, url_hash)
        os.makedirs(index_dir, exist_ok=True)
        try:
            self.vectorstore.save_local(index_dir)
            # Save chunk metadata separately (FAISS save_local stores docs
            # in the pickle, but we keep a JSON sidecar for inspection).
            meta = [
                {
                    "page_content": c.page_content,
                    "metadata": c.metadata,
                }
                for c in self.chunks
            ]
            meta_path = os.path.join(index_dir, "chunks_meta.json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)

            # Save embedding model metadata so load_from_disk can auto-select
            # the correct model (prevents vector-space mismatch).
            index_meta = {"embedding_model": self.embedding_model_name}
            index_meta_path = os.path.join(index_dir, "index_meta.json")
            with open(index_meta_path, "w", encoding="utf-8") as f:
                json.dump(index_meta, f)

            logger.info(
                "faiss.saved_to_disk",
                extra={"url_hash": url_hash, "chunks": len(self.chunks),
                       "embedding_model": self.embedding_model_name},
            )
        except Exception as exc:
            logger.warning(
                "faiss.save_failed",
                extra={"url_hash": url_hash, "error": str(exc)},
            )

    @staticmethod
    def get_disk_embedding_model(url_hash: str) -> str:
        """Read which embedding model was used for a saved FAISS index.

        Returns ``'fast'``, ``'accurate'``, or ``'fast'`` (default for
        legacy indexes that pre-date the metadata file).
        """
        meta_path = os.path.join(FAISS_INDEX_PATH, url_hash, "index_meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("embedding_model", "fast")
            except Exception:
                pass
        return "fast"  # legacy indexes assumed to be 'fast'

    @classmethod
    def load_from_disk(
        cls, url_hash: str, embeddings=None
    ) -> Optional["EnhancedRetriever"]:
        """Load a previously persisted FAISS index from disk.

        If *embeddings* is ``None``, the correct model is auto-selected
        from the saved ``index_meta.json`` sidecar.  This prevents the
        vector-space mismatch bug where an index built with one model
        is queried with a different model.

        Returns ``None`` if no index exists for *url_hash* or loading fails.
        """
        index_dir = os.path.join(FAISS_INDEX_PATH, url_hash)
        index_file = os.path.join(index_dir, "index.faiss")
        meta_file = os.path.join(index_dir, "chunks_meta.json")

        if not os.path.isfile(index_file):
            return None

        # Auto-select correct embedding model from saved metadata
        saved_model = cls.get_disk_embedding_model(url_hash)
        if embeddings is None:
            embeddings = (
                get_embeddings_accurate() if saved_model == "accurate"
                else get_embeddings_fast()
            )
            logger.info(
                "faiss.auto_select_model",
                extra={"url_hash": url_hash, "model": saved_model},
            )

        try:
            vectorstore = FAISS.load_local(
                index_dir,
                embeddings,
                allow_dangerous_deserialization=True,
            )
            # Reconstruct Document list from sidecar JSON
            chunks: List[Document] = []
            if os.path.isfile(meta_file):
                with open(meta_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                chunks = [
                    Document(
                        page_content=m["page_content"],
                        metadata=m.get("metadata", {}),
                    )
                    for m in meta
                ]
            else:
                # Fallback: extract docs from the vectorstore docstore
                chunks = list(vectorstore.docstore._dict.values())

            obj = cls.__new__(cls)
            obj.vectorstore = vectorstore
            obj.chunks = chunks
            obj._embeddings = embeddings
            obj.embedding_model_name = saved_model
            logger.info(
                "faiss.loaded_from_disk",
                extra={"url_hash": url_hash, "chunks": len(chunks),
                       "embedding_model": saved_model},
            )
            return obj
        except Exception as exc:
            logger.warning(
                "faiss.load_failed",
                extra={"url_hash": url_hash, "error": str(exc)},
            )
            return None

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_reranking: bool = True,
    ) -> List[Document]:
        """Retrieve the best *top_k* chunks for *query*."""
        candidates = self.vectorstore.similarity_search(query, k=min(top_k * 3, 20))

        if use_reranking and RERANK_AVAILABLE and candidates:
            candidates = self._rerank(query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        return self._diversity_filter(candidates, top_k)

    # ------------------------------------------------------------------
    # Reranking
    # ------------------------------------------------------------------

    def _rerank(
        self, query: str, docs: List[Document], top_k: int
    ) -> List[Document]:
        try:
            reranker = get_reranker()
            if reranker is None:
                return docs[:top_k]
            pairs = [[query, d.page_content] for d in docs]
            scores = reranker.predict(pairs)
            ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
            return [d for d, _ in ranked[:top_k]]
        except Exception as exc:
            logger.warning("reranking.failed", extra={"error": str(exc)})
            return docs[:top_k]

    # ------------------------------------------------------------------
    # Diversity filter
    # ------------------------------------------------------------------

    @staticmethod
    def _diversity_filter(docs: List[Document], top_k: int) -> List[Document]:
        if len(docs) <= top_k:
            return docs
        scored = sorted(
            docs,
            key=lambda d: d.metadata.get("importance_score", 0.5),
            reverse=True,
        )
        selected: List[Document] = []
        seen_types: set[str] = set()
        for doc in scored:
            ctype = doc.metadata.get("content_type", "text")
            if ctype not in seen_types or len(selected) < top_k:
                selected.append(doc)
                seen_types.add(ctype)
            if len(selected) >= top_k:
                break
        return selected
