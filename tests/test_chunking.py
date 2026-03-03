"""Tests for mcp_server.services.chunking — adaptive chunking strategy."""

from __future__ import annotations

import pytest

from mcp_server.services.chunking import AdaptiveChunkingStrategy


# ═══════════════════════════════════════════════════════════════════
# Adaptive parameter selection
# ═══════════════════════════════════════════════════════════════════

class TestGetAdaptiveParams:

    @pytest.mark.parametrize("doc_type", ["pdf", "docx", "pptx", "xlsx", "csv", "html"])
    def test_known_types_return_three_tuple(self, doc_type):
        chunk_size, overlap, seps = AdaptiveChunkingStrategy.get_adaptive_params(
            "x" * 10_000, doc_type
        )
        assert isinstance(chunk_size, int) and chunk_size > 0
        assert isinstance(overlap, int) and overlap > 0
        assert isinstance(seps, list) and len(seps) > 0

    def test_unknown_type_uses_defaults(self):
        cs, ov, seps = AdaptiveChunkingStrategy.get_adaptive_params("x" * 10_000, "unknown")
        assert cs == 1200
        assert ov == 250

    def test_large_document_scales_up(self):
        normal_cs, normal_ov, _ = AdaptiveChunkingStrategy.get_adaptive_params(
            "x" * 10_000, "pdf"
        )
        large_cs, large_ov, _ = AdaptiveChunkingStrategy.get_adaptive_params(
            "x" * 200_000, "pdf"
        )
        assert large_cs > normal_cs
        assert large_ov > normal_ov

    def test_small_document_scales_down(self):
        normal_cs, normal_ov, _ = AdaptiveChunkingStrategy.get_adaptive_params(
            "x" * 10_000, "pdf"
        )
        small_cs, small_ov, _ = AdaptiveChunkingStrategy.get_adaptive_params(
            "x" * 2_000, "pdf"
        )
        assert small_cs < normal_cs
        assert small_ov < normal_ov

    def test_minimum_chunk_size(self):
        """Even tiny documents should not produce zero-size chunks."""
        cs, ov, _ = AdaptiveChunkingStrategy.get_adaptive_params("hi", "pdf")
        assert cs >= 400


# ═══════════════════════════════════════════════════════════════════
# Chunk creation
# ═══════════════════════════════════════════════════════════════════

class TestCreateChunks:

    def test_returns_documents(self, sample_text):
        chunks = AdaptiveChunkingStrategy.create_chunks(sample_text, "pdf")
        assert len(chunks) > 0
        for c in chunks:
            assert hasattr(c, "page_content")
            assert hasattr(c, "metadata")

    def test_metadata_fields(self, sample_text):
        chunks = AdaptiveChunkingStrategy.create_chunks(sample_text, "pdf")
        first = chunks[0]
        assert "chunk_index" in first.metadata
        assert "total_chunks" in first.metadata
        assert "importance_score" in first.metadata
        assert "content_type" in first.metadata
        assert "doc_type" in first.metadata

    def test_chunk_index_sequential(self, sample_text):
        chunks = AdaptiveChunkingStrategy.create_chunks(sample_text, "pdf")
        indices = [c.metadata["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_total_chunks_consistent(self, sample_text):
        chunks = AdaptiveChunkingStrategy.create_chunks(sample_text, "pdf")
        for c in chunks:
            assert c.metadata["total_chunks"] == len(chunks)

    def test_empty_content(self):
        chunks = AdaptiveChunkingStrategy.create_chunks("", "pdf")
        # Depending on splitter behaviour, may return 0 or 1 tiny chunks
        assert isinstance(chunks, list)

    def test_doc_type_propagated(self, sample_text):
        for dt in ("pdf", "pptx", "xlsx"):
            chunks = AdaptiveChunkingStrategy.create_chunks(sample_text, dt)
            if chunks:
                assert chunks[0].metadata["doc_type"] == dt


# ═══════════════════════════════════════════════════════════════════
# Importance scoring
# ═══════════════════════════════════════════════════════════════════

class TestImportanceScore:

    def test_heading_boosts_score(self):
        plain = AdaptiveChunkingStrategy._importance_score(
            "This is just a simple paragraph about nothing in particular."
        )
        heading = AdaptiveChunkingStrategy._importance_score(
            "# Important Heading\n\nThis section is about critical results."
        )
        assert heading > plain

    def test_numbers_boost_score(self):
        plain = AdaptiveChunkingStrategy._importance_score(
            "This is a paragraph without any numbers at all."
        )
        numeric = AdaptiveChunkingStrategy._importance_score(
            "Revenue grew by 12.5% to $4.2 billion in Q3."
        )
        assert numeric > plain

    def test_keywords_boost_score(self):
        plain = AdaptiveChunkingStrategy._importance_score(
            "The weather is nice today and the sky is blue."
        )
        keyword = AdaptiveChunkingStrategy._importance_score(
            "The key finding is that the conclusion supports the recommendation."
        )
        assert keyword > plain

    def test_short_text_penalty(self):
        short = AdaptiveChunkingStrategy._importance_score("Hi.")
        long_text = AdaptiveChunkingStrategy._importance_score(
            "This is a reasonably long paragraph that discusses important findings."
        )
        assert short < long_text

    def test_score_bounded(self):
        """Score should always be in [0.0, 1.0]."""
        extremes = [
            "",
            "x",
            "# KEY RESULT\n$100 billion, 99.9%, critical summary conclusion",
            "x " * 10_000,
        ]
        for text in extremes:
            score = AdaptiveChunkingStrategy._importance_score(text)
            assert 0.0 <= score <= 1.0


# ═══════════════════════════════════════════════════════════════════
# Content type detection
# ═══════════════════════════════════════════════════════════════════

class TestDetectContentType:

    def test_table(self):
        assert AdaptiveChunkingStrategy._detect_content_type(
            "| Col1 | Col2 |\n|------|------|\n| a | b |"
        ) == "table"

    def test_list(self):
        assert AdaptiveChunkingStrategy._detect_content_type(
            "- Item one\n- Item two\n- Item three"
        ) == "list"

    def test_heading(self):
        assert AdaptiveChunkingStrategy._detect_content_type(
            "## Section Title\nSome content here"
        ) == "heading"

    def test_plain_text(self):
        assert AdaptiveChunkingStrategy._detect_content_type(
            "Just a normal paragraph of text."
        ) == "text"
