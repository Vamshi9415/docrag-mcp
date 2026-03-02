"""Extraction tools — per-format text / table / OCR extraction.

Each tool downloads a specific file type and returns extracted content
without running the full RAG pipeline.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from mcp_server.server import mcp
from mcp_server.middleware import guarded
from mcp_server.middleware.guards import validate_url

from mcp_server.services.downloader import download
from mcp_server.core.config import OCR_AVAILABLE


def _sanitize(text: str) -> str:
    """Remove null bytes and control characters that break JSON serialization."""
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text


# ───────────────────────────────────────────────────────────────────
# Tool 4 — Extract text from PDF
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_pdf_text(document_url: str) -> dict[str, Any]:
    """
    Download a PDF and extract its text content with layout preservation.

    Args:
        document_url: Public URL of a PDF file.

    Returns:
        Dict with 'text' (up to 50 000 chars) and 'char_count'.
    """
    validate_url(document_url)

    from mcp_server.processors.pdf import EnhancedPDFProcessor

    doc = await download(document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(
        None, EnhancedPDFProcessor.extract_pdf_content, doc
    )
    text = _sanitize(text)
    return {"text": text[:50_000], "char_count": len(text)}


# ───────────────────────────────────────────────────────────────────
# Tool 5 — Extract text from DOCX
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_docx_text(document_url: str) -> dict[str, Any]:
    """
    Download a DOCX file and extract text with headings and tables.

    Args:
        document_url: Public URL of a DOCX file.

    Returns:
        Dict with 'text' and 'char_count'.
    """
    validate_url(document_url)

    from mcp_server.processors.docx import extract_docx_text as _extract

    doc = await download(document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(None, _extract, doc)
    text = _sanitize(text)
    return {"text": text[:50_000], "char_count": len(text)}


# ───────────────────────────────────────────────────────────────────
# Tool 6 — Extract text from PPTX
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_pptx_text(document_url: str) -> dict[str, Any]:
    """
    Download a PPTX file and extract slide text, tables, and speaker notes.

    Args:
        document_url: Public URL of a PPTX file.

    Returns:
        Dict with 'text' and 'char_count'.
    """
    validate_url(document_url)

    from mcp_server.processors.pptx import EnhancedPPTXTextExtractor

    doc = await download(document_url)
    loop = asyncio.get_running_loop()
    text = await loop.run_in_executor(
        None, EnhancedPPTXTextExtractor.extract_text_from_pptx, doc
    )
    text = _sanitize(text)
    return {"text": text[:50_000], "char_count": len(text)}


# ───────────────────────────────────────────────────────────────────
# Tool 7 — Extract tables from XLSX
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_xlsx_tables(document_url: str) -> dict[str, Any]:
    """
    Download an XLSX file and extract all tables across all sheets.

    Returns structured content with column headers, row counts, data types,
    and cross-sheet relationships.

    Args:
        document_url: Public URL of an XLSX file.

    Returns:
        Dict with 'tables' list and 'table_count'.
    """
    validate_url(document_url)

    from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor

    doc = await download(document_url)
    loop = asyncio.get_running_loop()
    tables = await loop.run_in_executor(
        None, EnhancedXLSXTableExtractor.extract_tables_from_xlsx, doc
    )
    return {
        "tables": [
            {
                "content": t.content[:5000],
                "table_type": t.table_type,
                "location": t.location,
                "metadata": t.metadata,
            }
            for t in tables
        ],
        "table_count": len(tables),
    }


# ───────────────────────────────────────────────────────────────────
# Tool 8 — Extract tables from CSV
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_csv_tables(document_url: str) -> dict[str, Any]:
    """
    Download a CSV file and extract its tabular content.

    Args:
        document_url: Public URL of a CSV file.

    Returns:
        Dict with 'tables' list and 'table_count'.
    """
    validate_url(document_url)

    from mcp_server.processors.xlsx import EnhancedXLSXTableExtractor

    doc = await download(document_url)
    loop = asyncio.get_running_loop()
    tables = await loop.run_in_executor(
        None, EnhancedXLSXTableExtractor.extract_tables_from_csv, doc
    )
    return {
        "tables": [
            {
                "content": t.content[:5000],
                "table_type": t.table_type,
                "location": t.location,
                "metadata": t.metadata,
            }
            for t in tables
        ],
        "table_count": len(tables),
    }


# ───────────────────────────────────────────────────────────────────
# Tool 9 — OCR an image
# ───────────────────────────────────────────────────────────────────

@mcp.tool()
@guarded(timeout=120)
async def extract_image_text(image_url: str) -> dict[str, Any]:
    """
    Download a PNG or JPEG image and extract text via OCR (pytesseract).

    Args:
        image_url: Public URL of a PNG or JPEG image.

    Returns:
        Dict with 'ocr_results' (list of text blocks with confidence).
    """
    validate_url(image_url)

    if not OCR_AVAILABLE:
        return {"error": "pytesseract is not installed — OCR is unavailable."}

    from mcp_server.processors.image import ImageOCRProcessor

    request_id = str(uuid.uuid4())
    doc = await download(image_url)

    images = await ImageOCRProcessor.process_image_file(doc, image_url, request_id)

    return {
        "ocr_results": [
            {"text": img.ocr_text, "confidence": img.confidence, "metadata": img.metadata}
            for img in images
        ],
    }
