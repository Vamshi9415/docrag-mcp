"""Tests for mcp_server.core.errors — exception hierarchy."""

from __future__ import annotations

import pytest

from mcp_server.core.errors import (
    MCPServerError,
    RateLimitError,
    ValidationError,
    DownloadError,
    ProcessingError,
)


class TestMCPServerError:

    def test_base_defaults(self):
        e = MCPServerError("boom")
        assert e.message == "boom"
        assert e.code == "INTERNAL_ERROR"
        assert str(e) == "boom"

    def test_custom_code(self):
        e = MCPServerError("oops", code="CUSTOM")
        assert e.code == "CUSTOM"

    def test_is_exception(self):
        assert issubclass(MCPServerError, Exception)


class TestRateLimitError:

    def test_default_message(self):
        e = RateLimitError()
        assert e.code == "RATE_LIMITED"

    def test_catchable_as_mcp_error(self):
        with pytest.raises(MCPServerError):
            raise RateLimitError("too fast")


class TestValidationError:

    def test_code(self):
        e = ValidationError("bad url")
        assert e.code == "VALIDATION_ERROR"
        assert "bad url" in e.message


class TestDownloadError:

    def test_code(self):
        e = DownloadError("404")
        assert e.code == "DOWNLOAD_ERROR"


class TestProcessingError:

    def test_code(self):
        e = ProcessingError("corrupt pdf")
        assert e.code == "PROCESSING_ERROR"


class TestInheritanceChain:
    """All custom errors should be catchable via MCPServerError."""

    @pytest.mark.parametrize(
        "cls,args",
        [
            (RateLimitError, ()),
            (ValidationError, ("x",)),
            (DownloadError, ("x",)),
            (ProcessingError, ("x",)),
        ],
    )
    def test_catchable(self, cls, args):
        with pytest.raises(MCPServerError):
            raise cls(*args)
