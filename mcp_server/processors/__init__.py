"""Document processors — format-specific extraction and a type-dispatch registry.

Public API:
    detect_document_type(url)          → str
    TargetedDocumentProcessor.process_document(...)  → ProcessedDocument
"""

from __future__ import annotations

import logging
import os
import re
from typing import List
from urllib.parse import urlparse

from mcp_server.core.schemas import (
    ExtractedTable,
    ExtractedURL,
    ProcessedDocument,
)
from mcp_server.core.config import OCR_AVAILABLE
from mcp_server.services.language import detect_language_robust

logger = logging.getLogger("mcp_server.processors")


def _sanitize_text(text: str) -> str:
    """Remove null bytes and control characters that break JSON serialization."""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


# ---------------------------------------------------------------------------
# Document type detection
# ---------------------------------------------------------------------------

def detect_document_type(url: str) -> str:
    """Infer document type from the URL file extension."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    ext = os.path.splitext(path)[1].lstrip(".")

    ext_map = {
        "pdf": "pdf",
        "docx": "docx",
        "doc": "docx",
        "pptx": "pptx",
        "ppt": "pptx",
        "xlsx": "xlsx",
        "xls": "xlsx",
        "csv": "csv",
        "txt": "txt",
        "html": "html",
        "htm": "html",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
    }
    return ext_map.get(ext, "unknown")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class TargetedDocumentProcessor:
    """Route documents to the correct format-specific processor."""

    @staticmethod
    async def process_document(
        file_content: bytes,
        doc_type: str,
        file_path: str,
        request_id: str,
    ) -> ProcessedDocument:
        import asyncio

        loop = asyncio.get_running_loop()
        text = ""
        tables: List[ExtractedTable] = []
        images = []

        try:
            if doc_type == "pdf":
                from mcp_server.processors.pdf import EnhancedPDFProcessor

                text = await loop.run_in_executor(
                    None, EnhancedPDFProcessor.extract_pdf_content, file_content
                )

            elif doc_type == "docx":
                from mcp_server.processors.docx import extract_docx_text

                text = await loop.run_in_executor(None, extract_docx_text, file_content)

            elif doc_type == "pptx":
                from mcp_server.processors.pptx import EnhancedPPTXTextExtractor

                text = await loop.run_in_executor(
                    None, EnhancedPPTXTextExtractor.extract_text_from_pptx, file_content
                )

            elif doc_type == "xlsx":
                from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor

                tables = await loop.run_in_executor(
                    None, EnhancedXLSXTableExtractor.extract_tables_from_xlsx, file_content
                )
                text = "\n\n".join(t.content for t in tables)

            elif doc_type == "csv":
                from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor

                tables = await loop.run_in_executor(
                    None, EnhancedXLSXTableExtractor.extract_tables_from_csv, file_content
                )
                text = "\n\n".join(t.content for t in tables)

            elif doc_type == "image":
                if OCR_AVAILABLE:
                    from mcp_server.processors.image import ImageOCRProcessor

                    images = await ImageOCRProcessor.process_image_file(
                        file_content, file_path, request_id
                    )
                    text = "\n".join(img.ocr_text for img in images if img.ocr_text)

            elif doc_type in ("html", "txt"):
                text = file_content.decode("utf-8", errors="replace")
                if doc_type == "html":
                    try:
                        from bs4 import BeautifulSoup

                        soup = BeautifulSoup(text, "html.parser")
                        text = soup.get_text(separator="\n", strip=True)
                    except Exception:
                        pass  # keep raw text as fallback
            else:
                text = file_content.decode("utf-8", errors="replace")

        except Exception as exc:
            logger.error(f"Processing failed for {doc_type}: {exc}")
            text = file_content.decode("utf-8", errors="replace")

        # ── Extract URLs ──────────────────────────────────────────
        from mcp_server.processors.url import URLExtractor

        # Sanitize text to remove control chars that break JSON/SSE
        text = _sanitize_text(text) if text else text

        extracted_urls = URLExtractor.extract_urls(text) if text else []

        # ── Language detection ────────────────────────────────────
        lang = detect_language_robust(text) if text else "en"

        return ProcessedDocument(
            content=text,
            metadata={
                "doc_type": doc_type,
                "source": file_path,
                "content_length": len(text),
                "request_id": request_id,
            },
            tables=tables,
            images=images,
            extracted_urls=extracted_urls,
            detected_language=lang,
        )
