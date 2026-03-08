"""Tests for mcp_server.middleware.guards — rate limiting, validation."""

from __future__ import annotations

import time
import pytest

from mcp_server.core.errors import (
    RateLimitError,
    ValidationError,
)
from mcp_server.middleware.guards import (
    check_rate_limit,
    validate_url,
    validate_text,
    _TokenBucket,
    _get_user_bucket,
    _user_buckets,
    _user_buckets_lock,
)


# ═══════════════════════════════════════════════════════════════════
# Token Bucket
# ═══════════════════════════════════════════════════════════════════

class TestTokenBucket:

    def test_initial_capacity(self):
        bucket = _TokenBucket(rpm=10)
        assert bucket.capacity == 10.0
        assert bucket.tokens == 10.0

    def test_consume_decrements(self):
        bucket = _TokenBucket(rpm=5)
        assert bucket.consume() is True
        assert bucket.tokens == 4.0

    def test_exhaust_then_fail(self):
        bucket = _TokenBucket(rpm=3)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False  # exhausted

    def test_refill_over_time(self):
        bucket = _TokenBucket(rpm=60)  # 1 token/sec
        # Drain all tokens
        for _ in range(60):
            bucket.consume()
        assert bucket.consume() is False

        # Simulate 2 seconds passing
        bucket.last_refill = time.monotonic() - 2.0
        assert bucket.consume() is True  # refilled ~2 tokens


# ═══════════════════════════════════════════════════════════════════
# Rate Limiting
# ═══════════════════════════════════════════════════════════════════

class TestCheckRateLimit:

    def test_anonymous_allowed(self):
        """Calls without an api_key should use the 'anonymous' bucket."""
        # Should not raise for the first call
        check_rate_limit("test_tool", api_key="")

    def test_per_user_bucket_created(self):
        """Each unique API key gets its own bucket."""
        key = f"test-user-{time.monotonic()}"
        check_rate_limit("tool", api_key=key)
        bucket = _get_user_bucket(key)
        assert bucket is not None
        assert bucket.capacity > 0

    def test_rate_limit_exceeded(self):
        """Exhaust a user's bucket and verify RateLimitError is raised."""
        key = f"exhaust-user-{time.monotonic()}"
        bucket = _get_user_bucket(key)
        # Drain the bucket manually
        bucket.tokens = 0.0
        bucket.last_refill = time.monotonic()

        with pytest.raises(RateLimitError):
            check_rate_limit("tool", api_key=key)


# ═══════════════════════════════════════════════════════════════════
# URL Validation
# ═══════════════════════════════════════════════════════════════════

class TestValidateUrl:

    def test_valid_http(self):
        validate_url("http://example.com/file.pdf")

    def test_valid_https(self):
        validate_url("https://example.com/path/to/file.docx")

    def test_valid_with_query_params(self):
        validate_url("https://example.com/file?token=abc123&v=2")

    def test_empty_raises(self):
        with pytest.raises(ValidationError, match="non-empty"):
            validate_url("")

    def test_none_raises(self):
        with pytest.raises(ValidationError):
            validate_url(None)

    def test_no_scheme_raises(self):
        with pytest.raises(ValidationError, match="http"):
            validate_url("ftp://example.com/file")

    def test_plain_path_raises(self):
        with pytest.raises(ValidationError):
            validate_url("/local/path/file.pdf")

    def test_too_long_raises(self):
        long_url = "https://example.com/" + "a" * 3000
        with pytest.raises(ValidationError, match="maximum length"):
            validate_url(long_url)


# ═══════════════════════════════════════════════════════════════════
# Text Validation
# ═══════════════════════════════════════════════════════════════════

class TestValidateText:

    def test_valid_text(self):
        validate_text("What is the revenue?")

    def test_empty_text_ok(self):
        """Empty string is technically a valid string."""
        validate_text("")

    def test_non_string_raises(self):
        with pytest.raises(ValidationError, match="must be a string"):
            validate_text(12345)

    def test_too_long_raises(self):
        big = "x" * 200_000
        with pytest.raises(ValidationError, match="maximum length"):
            validate_text(big)

    def test_custom_field_name(self):
        with pytest.raises(ValidationError, match="query"):
            validate_text(42, field_name="query")
