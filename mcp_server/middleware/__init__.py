"""Middleware — the ``guarded`` decorator.

Apply it **inside** ``@mcp.tool()`` to wrap every tool invocation with:

1. Unique request-id assignment (``ContextVar``)
2. Authentication check
3. Global rate-limit check
4. ``asyncio`` timeout enforcement
5. Structured start / success / error logging
6. Exception → error-dict conversion (tools never raise to the client)

Usage::

    @mcp.tool()
    @guarded(timeout=300)
    async def my_tool(url: str) -> dict:
        ...
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
import uuid
from typing import Any

from mcp_server.core.config import security_config
from mcp_server.core.errors import MCPServerError
from mcp_server.core.logging import get_logger, request_id_var
from mcp_server.middleware.guards import check_auth, check_rate_limit

logger = get_logger("middleware")


def guarded(*, timeout: int | None = None):
    """Return a decorator that applies the full middleware chain.

    Parameters
    ----------
    timeout:
        Maximum seconds to wait for the tool handler.  Defaults to
        ``SecurityConfig.request_timeout`` (env ``MCP_REQUEST_TIMEOUT``,
        default 300 s).
    """
    _timeout = timeout if timeout is not None else security_config.request_timeout

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs) -> dict[str, Any]:
            rid = uuid.uuid4().hex[:12]
            token = request_id_var.set(rid)
            tool_name = fn.__name__
            start = time.monotonic()

            # ── Log incoming args ─────────────────────────────
            args_summary = {}
            try:
                sig = inspect.signature(fn)
                param_names = list(sig.parameters.keys())
                for i, a in enumerate(args):
                    key = param_names[i] if i < len(param_names) else f"arg{i}"
                    args_summary[key] = repr(a)[:200]
                for k, v in kwargs.items():
                    args_summary[k] = repr(v)[:200]
            except Exception:
                args_summary = {"raw_args": repr(args)[:200], "raw_kwargs": repr(kwargs)[:200]}

            try:
                # ── guards ────────────────────────────────────────
                check_auth()

                # Try to extract API key from kwargs for per-user rate limiting
                _api_key = ""
                if "api_key" in kwargs:
                    _api_key = str(kwargs.get("api_key", ""))
                check_rate_limit(tool_name, api_key=_api_key)

                logger.info(
                    "tool.start",
                    extra={"tool": tool_name, "tool_args": args_summary},
                )

                # ── execute with timeout ──────────────────────────
                result = await asyncio.wait_for(
                    fn(*args, **kwargs), timeout=_timeout
                )

                elapsed = time.monotonic() - start

                # ── Log result summary ────────────────────────────
                result_info = {}
                if isinstance(result, dict):
                    result_info["keys"] = list(result.keys())
                    # Preview each value (truncated)
                    for k, v in result.items():
                        v_str = repr(v)
                        result_info[k] = v_str[:300] + ("..." if len(v_str) > 300 else "")
                else:
                    result_info["type"] = type(result).__name__
                    result_info["preview"] = repr(result)[:500]

                logger.info(
                    "tool.success",
                    extra={
                        "tool": tool_name,
                        "elapsed": f"{elapsed:.2f}s",
                        "result_keys": result_info.get("keys"),
                        "result_preview": repr(result_info)[:1000],
                    },
                )
                return result

            except asyncio.TimeoutError:
                elapsed = time.monotonic() - start
                logger.error(
                    "tool.timeout",
                    extra={
                        "tool": tool_name,
                        "elapsed": f"{elapsed:.2f}s",
                        "tool_args": args_summary,
                        "error_detail": f"Timed out after {_timeout}s",
                    },
                )
                return {
                    "error": f"Operation timed out after {_timeout}s",
                    "code": "TIMEOUT",
                }

            except MCPServerError as exc:
                logger.warning(
                    "tool.known_error",
                    extra={
                        "tool": tool_name,
                        "code": exc.code,
                        "tool_args": args_summary,
                        "error_detail": exc.message,
                    },
                )
                return {"error": exc.message, "code": exc.code}

            except Exception as exc:
                logger.exception(
                    "tool.unhandled_error",
                    extra={
                        "tool": tool_name,
                        "tool_args": args_summary,
                        "error_detail": str(exc),
                    },
                )
                return {"error": f"Internal error: {exc}", "code": "INTERNAL_ERROR"}

            finally:
                request_id_var.reset(token)

        # Preserve original signature so FastMCP reads correct parameters
        wrapper.__signature__ = inspect.signature(fn)
        return wrapper

    return decorator
