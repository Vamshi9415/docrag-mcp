"""PDF processor — text extraction with layout preservation using PyMuPDF (fitz)."""

from __future__ import annotations

import logging
import re
from typing import List

import fitz  # PyMuPDF

logger = logging.getLogger("mcp_server.processors.pdf")


def _sanitize_text(text: str) -> str:
    """Remove null bytes and control characters that break JSON serialization."""
    # Remove null bytes
    text = text.replace("\x00", "")
    # Remove other problematic control characters (keep \n, \r, \t)
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


class EnhancedPDFProcessor:
    """Extract text from PDF bytes with structural awareness."""

    @staticmethod
    def extract_pdf_content(file_content: bytes) -> str:
        """Return the full text content of a PDF document."""
        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
            pages: List[str] = []

            for page_num in range(len(doc)):
                page = doc[page_num]

                # Dict-based extraction preserves layout better
                blocks = page.get_text("dict", sort=True).get("blocks", [])
                page_lines: List[str] = []

                for block in blocks:
                    if block.get("type") == 0:  # text block
                        for line in block.get("lines", []):
                            spans_text = " ".join(
                                span.get("text", "") for span in line.get("spans", [])
                            )
                            if spans_text.strip():
                                page_lines.append(spans_text.strip())

                if page_lines:
                    pages.append(
                        f"--- Page {page_num + 1} ---\n" + "\n".join(page_lines)
                    )

            doc.close()
            return _sanitize_text("\n\n".join(pages))

        except Exception as exc:
            logger.error(f"PDF extraction failed: {exc}")
            # Fallback: raw page text
            try:
                doc = fitz.open(stream=file_content, filetype="pdf")
                text = "\n\n".join(
                    page.get_text() for page in doc
                )
                doc.close()
                return _sanitize_text(text)
            except Exception as fallback_exc:
                logger.error(f"PDF fallback also failed: {fallback_exc}")
                return ""
