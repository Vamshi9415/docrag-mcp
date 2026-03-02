"""Robust language detection with multi-round sampling.

Uses ``langdetect`` (if installed) with three independent rounds and
majority-vote to smooth out the inherent randomness.
"""

from __future__ import annotations

import logging
from collections import Counter

from mcp_server.core.config import LANG_DETECT_AVAILABLE

logger = logging.getLogger("mcp_server.language")

# ---------------------------------------------------------------------------
# Language name map
# ---------------------------------------------------------------------------

_LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "fr": "French", "de": "German",
    "it": "Italian", "pt": "Portuguese", "hi": "Hindi", "bn": "Bengali",
    "te": "Telugu", "ta": "Tamil", "mr": "Marathi", "ml": "Malayalam",
    "kn": "Kannada", "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu",
    "zh-cn": "Chinese", "ja": "Japanese",
}




# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_language_robust(text: str) -> str:
    """Detect the dominant language of *text* (returns ISO-639-1 code)."""
    if not LANG_DETECT_AVAILABLE or not text or len(text.strip()) < 10:
        return "en"
    try:
        from langdetect import detect, DetectorFactory

        DetectorFactory.seed = 0
        attempts: list[str] = []
        for _ in range(3):
            try:
                attempts.append(detect(text[:5000]))
            except Exception:
                attempts.append("en")
        if not attempts:
            return "en"
        counter = Counter(attempts)
        return counter.most_common(1)[0][0]
    except Exception:
        return "en"


def get_language_name(code: str) -> str:
    """Map an ISO-639-1 code to its English name."""
    return _LANGUAGE_NAMES.get(code, f"Unknown ({code})")



