"""URL extractor — find and categorise URLs in document text."""

from __future__ import annotations

import re
from typing import List

from mcp_server.core.schemas import ExtractedURL


class URLExtractor:
    """Extract, validate, and categorise URLs from free text."""

    @staticmethod
    def extract_urls(text: str) -> List[ExtractedURL]:
        """Return a list of ``ExtractedURL`` with surrounding context."""
        results: List[ExtractedURL] = []
        url_re = re.compile(r'https?://[^\s<>"\']+|www\.[^\s<>"\']+\.[^\s<>"\']+')

        for match in url_re.finditer(text):
            url = match.group()
            if not url.startswith("http"):
                url = "http://" + url

            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end].strip()

            results.append(ExtractedURL(
                url=url,
                context=context,
                source_location=f"Position {match.start()}",
                confidence=0.9,
                url_type=URLExtractor._categorize(url, context),
            ))

        return results

    @staticmethod
    def _categorize(url: str, context: str) -> str:
        ul = url.lower()
        cl = context.lower()
        if any(t in ul for t in ("api", "endpoint")):
            return "api_endpoint"
        if any(t in cl for t in ("click", "link", "visit")):
            return "navigation"
        if any(t in ul for t in ("image", "img", "photo", "png", "jpg")):
            return "image"
        return "general"
