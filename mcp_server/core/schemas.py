"""Domain data-classes shared across the server.

All structures are plain ``@dataclass`` instances (no Pydantic) so the
package stays lightweight and serialisation is explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ExtractedTable:
    content: str
    table_type: str = "unknown"
    location: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedImage:
    image_path: str
    ocr_text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0


@dataclass
class ExtractedURL:
    url: str
    context: str = ""
    source_location: str = ""
    confidence: float = 0.0
    url_type: str = "general"


@dataclass
class ProcessedDocument:
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: List[ExtractedTable] = field(default_factory=list)
    images: List[ExtractedImage] = field(default_factory=list)
    extracted_urls: List[ExtractedURL] = field(default_factory=list)
    detected_language: str = "en"
