"""CLI entry point — ``python -m mcp_server``."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mcp_server",
        description="RAG Document MCP Server — production tools for AI agents",
    )
    parser.add_argument(
        "--transport",
        choices=["streamable-http", "stdio", "rest"],
        default="streamable-http",
        help="Transport: streamable-http (MCP), stdio (MCP), or rest (plain REST API, default: streamable-http)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Auto-reload server when code changes (dev mode)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of uvicorn worker processes (default: 1, recommended: 4 for production)",
    )
    args = parser.parse_args()

    if args.transport == "rest":
        # ── Plain REST API (FastAPI + Uvicorn) ────────────────────
        import uvicorn

        workers = args.workers if not args.reload else 1
        print(f"\n  RAG Document Server — REST API")
        print(f"  Listening on http://{args.host}:{args.port}")
        if workers > 1:
            print(f"  Workers: {workers}")
        print(f"  Swagger docs: http://{args.host}:{args.port}/docs\n")
        uvicorn.run(
            "mcp_server.api:create_rest_app",
            factory=True,
            host=args.host,
            port=args.port,
            reload=args.reload,
            workers=workers,
        )
    else:
        # ── MCP transport (streamable-http or stdio) ──────────────
        if args.reload:
            # Use uvicorn with reload to serve the MCP ASGI app
            # NOTE: --reload and --workers>1 are mutually exclusive in uvicorn
            import uvicorn
            print(f"\n  RAG Document Server — MCP (streamable-http) [DEV MODE — auto-reload]")
            print(f"  Listening on http://{args.host}:{args.port}\n")
            uvicorn.run(
                "mcp_server._asgi:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                reload=True,
                reload_dirs=["mcp_server"],
                reload_excludes=[
                    "__pycache__",
                    "*.pyc",
                    "request_logs",
                    "temp_files",
                ],
            )
        elif args.transport == "streamable-http":
            import uvicorn

            workers = args.workers
            print(f"\n  RAG Document Server \u2014 MCP (streamable-http)")
            print(f"  Listening on  http://{args.host}:{args.port}")
            print(f"  MCP endpoint: http://{args.host}:{args.port}/mcp")
            if workers > 1:
                print(f"  Workers: {workers}")
            print(f"  Auth: {'enabled (x-api-key required)' if __import__('os').getenv('MCP_API_KEY') else 'disabled (set MCP_API_KEY to enable)'}\n")
            uvicorn.run(
                "mcp_server._asgi:create_app",
                factory=True,
                host=args.host,
                port=args.port,
                workers=workers,
            )
        else:
            from mcp_server.server import create_server
            mcp = create_server(host=args.host, port=args.port)
            mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
