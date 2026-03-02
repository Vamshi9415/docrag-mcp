"""Exception hierarchy for the MCP server.

Every custom exception carries a human-readable *message* and a stable
*code* string that MCP clients can match on programmatically.
"""

from __future__ import annotations


class MCPServerError(Exception):
    """Base exception for all server errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class AuthenticationError(MCPServerError):
    """Raised when API-key validation fails."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="AUTH_ERROR")


class RateLimitError(MCPServerError):
    """Raised when a client exceeds the request-rate budget."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, code="RATE_LIMITED")


class ValidationError(MCPServerError):
    """Raised for invalid or malicious input."""

    def __init__(self, message: str):
        super().__init__(message, code="VALIDATION_ERROR")


class DownloadError(MCPServerError):
    """Raised when a document download fails after retries."""

    def __init__(self, message: str):
        super().__init__(message, code="DOWNLOAD_ERROR")


class ProcessingError(MCPServerError):
    """Raised when document processing or querying fails."""

    def __init__(self, message: str):
        super().__init__(message, code="PROCESSING_ERROR")


class ModelLoadError(MCPServerError):
    """Raised when an ML model fails to initialise."""

    def __init__(self, message: str):
        super().__init__(message, code="MODEL_LOAD_ERROR")
