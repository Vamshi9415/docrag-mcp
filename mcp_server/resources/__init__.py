"""MCP Resources — read-only context exposed to MCP clients."""

from mcp_server.server import mcp


@mcp.resource("rag://supported-formats")
def supported_formats() -> str:
    """List of all supported document formats and their processing capabilities."""
    return (
        "Supported formats:\n"
        "  Documents : PDF, DOCX, PPTX, TXT, HTML\n"
        "  Tables    : XLSX, CSV\n"
        "  Images    : PNG, JPEG, JPG (via OCR)\n\n"
        "Each format has a specialised processor that extracts text, tables,\n"
        "images, URLs, and metadata for downstream RAG retrieval."
    )


@mcp.resource("rag://tool-descriptions")
def tool_descriptions() -> str:
    """Summary of available tools and when to use each one."""
    return (
        "Available tools (no LLM inside — bring your own):\n\n"
        "  process_document   — Extract text, tables, images, URLs from any supported format\n"
        "  chunk_document     — Split document into scored RAG-ready chunks\n"
        "  retrieve_chunks    — Vector search (FAISS + reranking) to find relevant chunks\n"
        "  extract_pdf_text   — PDF-specific text extraction with layout\n"
        "  extract_docx_text  — DOCX text with headings and tables\n"
        "  extract_pptx_text  — PPTX slides, notes, tables\n"
        "  extract_xlsx_tables— XLSX multi-sheet table extraction\n"
        "  extract_csv_tables — CSV tabular content extraction\n"
        "  extract_image_text — OCR via pytesseract\n"
        "  detect_language    — Language detection\n"
        "  get_system_health  — Server health and capabilities\n"
        "  manage_cache       — View or clear caches\n"
    )
