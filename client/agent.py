from __future__ import annotations
import asyncio
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent


# ─────────────────────────────────────────────
# LLM Setup
# ─────────────────────────────────────────────
def get_llm():
    """Return a ChatModel — Gemini first, OpenAI fallback."""
    
    if os.getenv("GOOGLE_API_KEY"):
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0,
        )

    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
        )

    raise RuntimeError("Set GOOGLE_API_KEY or OPENAI_API_KEY in .env")


# ─────────────────────────────────────────────
# MCP Config
# ─────────────────────────────────────────────
MCP_SERVER_URL = os.getenv(
    "MCP_SERVER_URL",
    "http://127.0.0.1:8000/mcp"
)

SYSTEM_PROMPT = """You are a document analysis assistant. You MUST use tools to answer questions.

RULE #1 — MANDATORY: When the user asks ANY question about ANY document URL, you MUST
call a tool. NEVER refuse. NEVER say "I can't". NEVER say the file type is unsupported.

TOOL SELECTION:

• **retrieve_chunks(document_url, query)** — Use for ANY question about ANY document
  (PDF, DOCX, PPTX, XLSX, CSV, HTML, images). This is the DEFAULT tool.
  It extracts text, builds a vector index, and returns the most relevant passages.
  Example: retrieve_chunks(document_url="http://...", query="phone number of vamshi")

• **query_spreadsheet(document_url, search_value)** — Use ONLY for XLSX/CSV when the
  URL ends in .xlsx or .csv AND the user wants a specific row lookup.

• **extract_* tools** — Only when the user says "extract" or "dump".

AFTER RECEIVING TOOL RESULTS:
- Read the returned text carefully.
- Answer the user's question with specific data from the results.
- If the data is not found in the results, say "The document does not contain ..."
- NEVER return an empty answer.
"""

agent = None


# ─────────────────────────────────────────────
# Initialize Agent (RUNS ONLY ONCE)
# ─────────────────────────────────────────────
async def initialize_agent():

    global agent

    llm = get_llm()

    client = MultiServerMCPClient(
        {
            "rag-server": {
                "url": MCP_SERVER_URL,
                "transport": "streamable_http",
            }
        }
    )

    tools = await client.get_tools()

    print(f"\nConnected to MCP server: {MCP_SERVER_URL}")
    print(f"Loaded tools: {[t.name for t in tools]}\n")

    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)


# ─────────────────────────────────────────────
# Run Query
# ─────────────────────────────────────────────
async def run_query(query: str):

    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": query}]}
    )

    # Print tool calls and responses for visibility
    last_tool_result = None
    for m in result["messages"]:
        if m.type == "ai" and hasattr(m, "tool_calls") and m.tool_calls:
            for tc in m.tool_calls:
                args_summary = ", ".join(f"{k}={repr(v)[:80]}" for k, v in tc["args"].items())
                print(f"  [TOOL CALL] {tc['name']}({args_summary})")
        if m.type == "tool":
            last_tool_result = m.content
            content_preview = str(m.content)[:200]
            print(f"  [TOOL RESULT] {m.name} → {content_preview}...")

    # Extract the final AI answer
    answer = _extract_ai_answer(result["messages"])

    # Fallback: if the LLM returned empty but we have tool results,
    # format the tool output directly so the user still gets an answer.
    if answer == "(no response)" and last_tool_result:
        answer = _fallback_from_tool_result(last_tool_result)

    return answer


def _extract_ai_answer(messages) -> str:
    """Pull the last non-empty AI text from the message list."""
    for m in reversed(messages):
        if m.type != "ai":
            continue
        content = m.content
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, str) and p.strip():
                    parts.append(p)
                elif isinstance(p, dict) and p.get("text", "").strip():
                    parts.append(p["text"])
                elif hasattr(p, "text") and getattr(p, "text", "").strip():
                    parts.append(p.text)
            if parts:
                return "\n".join(parts)
    return "(no response)"


def _fallback_from_tool_result(content) -> str:
    """Best-effort human-readable answer from raw tool output."""
    import json

    text = content if isinstance(content, str) else str(content)

    # MCP tool results come as [{"type":"text","text":"..."}]
    try:
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    text = item["text"]
                    break
    except Exception:
        pass

    # Try to parse JSON and produce a readable summary
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            # query_spreadsheet result
            if "matches" in data and data.get("match_count", 0) > 0:
                rows = data["matches"]
                lines = []
                for row in rows[:5]:
                    parts = [f"{k}: {v}" for k, v in row.items() if k != "_sheet"]
                    lines.append(", ".join(parts))
                return "Found:\n" + "\n".join(f"  • {l}" for l in lines)

            # retrieve_chunks result
            if "results" in data and data["results"]:
                top = data["results"][0]
                return f"Top result:\n{top.get('text', str(top))[:500]}"

        return text[:500]
    except (json.JSONDecodeError, TypeError):
        return text[:500]


# ─────────────────────────────────────────────
# Interactive Chat
# ─────────────────────────────────────────────
async def interactive():

    print("\nLangChain MCP Agent")
    print("Type 'quit' to exit\n")

    while True:

        query = input("> ").strip()

        if query.lower() in ("quit", "exit", "q"):
            break

        answer = await run_query(query)

        print("\n", answer, "\n")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
async def main():

    await initialize_agent()

    if len(sys.argv) > 1:

        query = " ".join(sys.argv[1:])
        answer = await run_query(query)
        print(answer)

    else:

        await interactive()


if __name__ == "__main__":
    asyncio.run(main())