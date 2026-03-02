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
    args = parser.parse_args()

    if args.transport == "rest":
        # ── Plain REST API (FastAPI + Uvicorn) ────────────────────
        import uvicorn
        from mcp_server.api import create_rest_app

        app = create_rest_app()
        print(f"\n  RAG Document Server — REST API")
        print(f"  Listening on http://{args.host}:{args.port}")
        print(f"  Swagger docs: http://{args.host}:{args.port}/docs\n")
        uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)
    else:
        # ── MCP transport (streamable-http or stdio) ──────────────
        if args.reload:
            # Use uvicorn with reload to serve the MCP ASGI app
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
        else:
            from mcp_server.server import create_server
            from mcp_server.middleware.guards import AuthMiddleware, MCPRouter

            mcp = create_server(host=args.host, port=args.port)

            if args.transport == "streamable-http":
                # Build the ASGI app, add GET routes, then wrap with auth.
                # Stack: AuthMiddleware -> MCPRouter -> FastMCP
                import uvicorn

                asgi_app = AuthMiddleware(MCPRouter(mcp.streamable_http_app()))
                print(f"\n  RAG Document Server \u2014 MCP (streamable-http)")
                print(f"  Listening on  http://{args.host}:{args.port}")
                print(f"  MCP endpoint: http://{args.host}:{args.port}/mcp")
                print(f"  Auth: {'enabled (x-api-key required)' if __import__('os').getenv('MCP_API_KEY') else 'disabled (set MCP_API_KEY to enable)'}\n")
                uvicorn.run(asgi_app, host=args.host, port=args.port)
            else:
                mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
