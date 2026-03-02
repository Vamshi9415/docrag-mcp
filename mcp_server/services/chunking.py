"""Adaptive chunking strategy for RAG retrieval.

Chunk sizes, overlaps, and separators are tuned dynamically based on the
document type (PDF, PPTX, XLSX, …) and its overall length.  Each chunk
is scored for *importance* so the retriever can prefer chunks with
headings, numbers, or key terms.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document


class AdaptiveChunkingStrategy:
    """Create context-aware chunks with importance scoring."""

    @staticmethod
    def get_adaptive_params(
        content: str, doc_type: str
    ) -> Tuple[int, int, List[str]]:
        """Return ``(chunk_size, overlap, separators)`` tuned for the input."""
        length = len(content)

        params_map = {
            "pdf": (1500, 300, ["\n\n", "\n", ". ", " "]),
            "pptx": (800, 150, ["\n---\n", "\n\n", "\n", ". ", " "]),
            "xlsx": (1200, 200, ["\n===", "\n---", "\n\n", "\n", " "]),
            "csv": (1200, 200, ["\n===", "\n---", "\n\n", "\n", " "]),
            "docx": (1500, 300, ["\n\n", "\n", ". ", " "]),
            "html": (1500, 300, ["\n\n", "\n", ". ", " "]),
        }
        chunk_size, overlap, separators = params_map.get(
            doc_type, (1200, 250, ["\n\n", "\n", ". ", " "])
        )

        if length > 100_000:
            chunk_size = int(chunk_size * 1.5)
            overlap = int(overlap * 1.3)
        elif length < 5_000:
            chunk_size = max(400, chunk_size // 2)
            overlap = max(50, overlap // 2)

        return chunk_size, overlap, separators

    @staticmethod
    def create_chunks(content: str, doc_type: str) -> List[Document]:
        """Split *content* into scored ``Document`` chunks."""
        chunk_size, overlap, separators = (
            AdaptiveChunkingStrategy.get_adaptive_params(content, doc_type)
        )
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            separators=separators,
            length_function=len,
        )
        raw = splitter.create_documents([content])

        scored: List[Document] = []
        for idx, doc in enumerate(raw):
            score = AdaptiveChunkingStrategy._importance_score(doc.page_content)
            doc.metadata.update(
                {
                    "chunk_index": idx,
                    "total_chunks": len(raw),
                    "importance_score": score,
                    "content_type": AdaptiveChunkingStrategy._detect_content_type(
                        doc.page_content
                    ),
                    "doc_type": doc_type,
                }
            )
            scored.append(doc)

        return scored

    # ------------------------------------------------------------------
    # Internal scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _importance_score(text: str) -> float:
        score = 0.5
        heading_re = re.compile(r"^#{1,3}\s|^[A-Z][A-Z\s]{3,}$", re.MULTILINE)
        if heading_re.search(text):
            score += 0.2
        if re.search(r"\d+\.?\d*%|\$\d+|€\d+", text):
            score += 0.15
        keywords = [
            "important",
            "key",
            "critical",
            "summary",
            "conclusion",
            "result",
            "finding",
            "recommendation",
        ]
        if any(kw in text.lower() for kw in keywords):
            score += 0.1
        if len(text) < 50:
            score -= 0.2
        return round(min(1.0, max(0.0, score)), 2)

    @staticmethod
    def _detect_content_type(text: str) -> str:
        if re.search(r"[|].*[|]", text) or "\t" in text:
            return "table"
        if re.search(r"^\s*[-•*]\s", text, re.MULTILINE):
            return "list"
        if re.search(r"^#{1,6}\s", text, re.MULTILINE):
            return "heading"
        return "text"
