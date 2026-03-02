"""Enhanced retriever — FAISS vector search with cross-encoder reranking.

Builds a FAISS index on the fly from chunked documents and, when the
``cross-encoder`` model is available, reranks the top-K results for
higher precision.  An importance-based diversity filter promotes chunks
with varied content types.
"""

from __future__ import annotations

from typing import List

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS

from mcp_server.core.config import RERANK_AVAILABLE
from mcp_server.core.models import get_reranker
from mcp_server.core.logging import get_logger

logger = get_logger("retrieval")


class EnhancedRetriever:
    """Build a FAISS index and retrieve the best chunks for a query."""

    def __init__(self, embeddings, chunks: List[Document]):
        self.vectorstore = FAISS.from_documents(chunks, embeddings)
        self.chunks = chunks

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
