"""Shared fixtures for the MCP Server test suite.

Usage:
    pytest tests/ -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil

import pytest

# Ensure the project root is on sys.path so `mcp_server` is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ── Environment fixtures ──────────────────────────────────────────

@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Set deterministic env vars for tests."""
    pass


@pytest.fixture()
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    d = tempfile.mkdtemp(prefix="mcp_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def sample_text():
    """A representative chunk of English prose for chunking / language tests."""
    return (
        "# Introduction\n\n"
        "This report summarises the key findings from the Q3 analysis. "
        "Revenue grew by 12.5% year-over-year, reaching $4.2 billion. "
        "The critical recommendation is to expand into APAC markets.\n\n"
        "## Methodology\n\n"
        "We collected data from 150 sources across 12 countries. "
        "Each data point was validated through a three-step verification "
        "process to ensure accuracy and reliability.\n\n"
        "## Results\n\n"
        "| Region | Revenue | Growth |\n"
        "|--------|---------|--------|\n"
        "| NA     | $2.1B   | 10%    |\n"
        "| EMEA   | $1.5B   | 15%    |\n"
        "| APAC   | $0.6B   | 22%    |\n\n"
        "The conclusion is that APAC shows the strongest potential "
        "for future investment.\n"
    )


@pytest.fixture()
def sample_csv_bytes():
    """Small CSV file as bytes for upload / extraction tests."""
    return (
        b"Name,Age,Score\n"
        b"Alice,25,92.5\n"
        b"Bob,30,88.0\n"
        b"Carol,28,95.3\n"
    )
