"""LangChain agent that connects to the MCP server and uses its tools.

The MCP server is a **pure deterministic tool server** — all LLM reasoning
happens here in the client.

Usage
─────
1. Start the MCP server (in a separate terminal):
       python -m mcp_server --transport streamable-http

2. Copy .env.example → .env and add your GOOGLE_API_KEY (or OPENAI_API_KEY).

3. Run this agent:
       python agent.py "Summarise the key points of https://example.com/report.pdf"

   or interactively:
       python agent.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# ─── LLM setup ──────────────────────────────────────────────────

def get_llm():
    """Return a ChatModel — Gemini by default, OpenAI as fallback."""
    if os.getenv("GOOGLE_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,
        )
    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model="gpt-4o-mini", temperature=0)
    print("ERROR: Set GOOGLE_API_KEY or OPENAI_API_KEY in .env")
    sys.exit(1)


# ─── MCP connection ─────────────────────────────────────────────

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:8000/mcp")


async def run_agent(query: str) -> str:
    """Connect to MCP server, load tools, run the agent, return answer."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from langgraph.prebuilt import create_react_agent

    llm = get_llm()

    async with MultiServerMCPClient(
        {
            "rag-server": {
                "url": MCP_SERVER_URL,
                "transport": "streamable_http",
            }
        }
    ) as mcp_client:
        tools = mcp_client.get_tools()

        print(f"\n  Connected to MCP server at {MCP_SERVER_URL}")
        print(f"  Loaded {len(tools)} tools: {[t.name for t in tools]}\n")

        agent = create_react_agent(llm, tools)

        result = agent.invoke({"messages": [{"role": "user", "content": query}]})

        # Extract the final AI message
        ai_messages = [m for m in result["messages"] if m.type == "ai" and m.content]
        return ai_messages[-1].content if ai_messages else "(no response)"


# ─── Interactive loop ────────────────────────────────────────────

async def interactive():
    """REPL loop — type queries, get answers."""
    print("╔═══════════════════════════════════════════════════════╗")
    print("║   LangChain Agent — MCP Tool Client                  ║")
    print("║   Type a question or 'quit' to exit.                 ║")
    print("╚═══════════════════════════════════════════════════════╝")

    while True:
        try:
            query = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not query or query.lower() in ("quit", "exit", "q"):
            break

        try:
            answer = await run_agent(query)
            print(f"\n{answer}")
        except Exception as exc:
            print(f"\nError: {exc}")


# ─── CLI entry point ─────────────────────────────────────────────

async def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        answer = await run_agent(query)
        print(answer)
    else:
        await interactive()


if __name__ == "__main__":
    asyncio.run(main())
