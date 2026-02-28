# Client — LangChain MCP Agent

A **separate process** that connects to the running RAG Document Server via
the Model Context Protocol (MCP) and uses its 12 tools through an
LLM-powered ReAct agent.

> The MCP server itself contains **no LLM** — all reasoning happens here in the client.

```
┌─────────────────────────────┐     MCP (streamable-http)     ┌──────────────────────────┐
│   client/agent.py           │ ◄──────────────────────────► │  MCP Server              │
│                             │                               │  (pure tools, no LLM)    │
│  ┌───────────────────────┐  │   tool calls:                 │                          │
│  │  LLM (Gemini/OpenAI)  │  │   ─ process_document          │  • process_document      │
│  │  ReAct Agent           │  │   ─ chunk_document            │  • chunk_document        │
│  │  Reasoning + Answers   │  │   ─ retrieve_chunks           │  • retrieve_chunks       │
│  └───────────────────────┘  │   ─ extract_pdf_text           │  • extract_*_text/tables │
│                             │   ─ detect_language             │  • detect_language       │
│  Decision: which tool(s)    │   ─ manage_cache               │  • get_system_health     │
│  to call, how to combine    │                               │  • manage_cache          │
│  results, what to answer    │                               │                          │
└─────────────────────────────┘                               └──────────────────────────┘
```

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [How It Works](#how-it-works)
3. [LLM Selection](#llm-selection)
4. [Usage Modes](#usage-modes)
5. [Available Tools (from MCP Server)](#available-tools-from-mcp-server)
6. [Environment Variables](#environment-variables)
7. [Example Conversations](#example-conversations)
8. [Architecture Details](#architecture-details)
9. [Troubleshooting](#troubleshooting)

---

## Quick Start

### 1. Start the MCP server (in a separate terminal)

```bash
cd ..
python -m mcp_server --transport streamable-http
# Or: python -m mcp_server  (same default)
```

### 2. Install client dependencies

```bash
cd client
pip install -r requirements.txt
```

**Dependencies:** `langchain`, `langchain-google-genai`, `langchain-mcp-adapters`, `python-dotenv`

### 3. Configure your LLM key

```bash
cp .env.example .env
# Edit .env and add at least one:
#   GOOGLE_API_KEY=your-gemini-key
#   OPENAI_API_KEY=your-openai-key
```

### 4. Run the agent

```bash
# Interactive REPL mode
python agent.py

# One-shot mode (single query, then exit)
python agent.py "Extract and summarise the key points from https://example.com/report.pdf"
```

---

## How It Works

The client uses a **ReAct (Reasoning + Acting)** agent pattern from LangGraph:

```
User Query
    │
    ▼
┌──────────────────┐
│  LLM (Gemini)    │   "I need to retrieve chunks about key findings"
│  Reasoning step   │
└────────┬─────────┘
         │  tool call: retrieve_chunks(url, query, top_k=5)
         ▼
┌──────────────────┐
│  MCP Server      │   Returns 5 relevant chunks with scores
│  Tool execution   │
└────────┬─────────┘
         │  tool result
         ▼
┌──────────────────┐
│  LLM (Gemini)    │   "Based on these chunks, the key findings are..."
│  Final answer     │
└──────────────────┘
```

1. The agent receives a user query
2. The LLM **decides** which tool(s) to call (it can call multiple tools in sequence)
3. Each tool call is sent to the MCP server over `streamable-http`
4. The server executes the tool and returns structured results
5. The LLM synthesises the results into a natural-language answer
6. If needed, the LLM can make additional tool calls before answering

### MCP Connection

The client uses `MultiServerMCPClient` from `langchain-mcp-adapters` which:
- Connects to the MCP server at `MCP_SERVER_URL` (default `http://127.0.0.1:8000/mcp`)
- Uses `streamable_http` transport
- Automatically discovers all available tools from the server
- Converts MCP tool schemas into LangChain-compatible tool objects

---

## LLM Selection

The agent supports two LLM providers, selected automatically based on
which API key is available:

| Priority | Provider | Env Variable | Model | Temperature |
|----------|----------|-------------|-------|-------------|
| 1 (default) | Google Gemini | `GOOGLE_API_KEY` | `gemini-2.5-flash` | `0` |
| 2 (fallback) | OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` | `0` |

**Selection logic:**
1. If `GOOGLE_API_KEY` is set → use Gemini (even if OpenAI key is also set)
2. Else if `OPENAI_API_KEY` is set → use OpenAI
3. Else → exit with error: `"Set GOOGLE_API_KEY or OPENAI_API_KEY in .env"`

Temperature is set to `0` for deterministic, reproducible responses.

---

## Usage Modes

### Interactive REPL

```bash
python agent.py
```

Starts an interactive loop where you can ask multiple questions:

```
> Summarise this PDF: https://example.com/report.pdf
[Agent reasoning + tool calls + answer]

> What language is this text? "Bonjour, comment allez-vous?"
[Agent calls detect_language tool + answer]

> quit
```

Type `quit`, `exit`, or `q` to end the session.

### One-Shot Mode

```bash
python agent.py "Your question here"
```

Processes a single query, prints the answer, and exits. Useful for scripting
and CI/CD pipelines.

### Examples

```bash
# Analyse a PDF
python agent.py "What are the main conclusions in https://example.com/annual-report.pdf?"

# Extract data from a spreadsheet
python agent.py "Show the tables from https://example.com/data.xlsx"

# Process a CSV file
python agent.py "Summarise the data in https://example.com/metrics.csv"

# OCR an image
python agent.py "Extract text from https://example.com/receipt.jpg"

# Check server health
python agent.py "What's the server status?"
```

---

## Available Tools (from MCP Server)

The agent automatically discovers all 12 tools from the MCP server:

### Document Tools

| Tool | Description | Use When |
|------|-------------|----------|
| `process_document` | Extract text, tables, images, URLs from any document | You need the full content of a document |
| `chunk_document` | Split document into scored, RAG-ready chunks | You need to see how a document is segmented |
| `retrieve_chunks` | FAISS vector search + reranking for relevant chunks | You need to answer a specific question about a document |

### Extraction Tools

| Tool | Description | Use When |
|------|-------------|----------|
| `extract_pdf_text` | PDF text extraction with layout preservation | You only need raw text from a PDF |
| `extract_docx_text` | DOCX text with headings and tables | Working with Word documents |
| `extract_pptx_text` | PPTX slides, notes, tables, hyperlinks | Working with PowerPoint files |
| `extract_xlsx_tables` | Multi-sheet XLSX table extraction with metadata | Working with Excel spreadsheets |
| `extract_csv_tables` | CSV tabular content extraction | Working with CSV files |
| `extract_image_text` | OCR via pytesseract with confidence scores | Extracting text from images |

### Utility Tools

| Tool | Description | Use When |
|------|-------------|----------|
| `detect_language` | Multi-round language detection | Identifying the language of text |
| `get_system_health` | Server health, models, capabilities, cache stats | Checking server status |
| `manage_cache` | Cache inspection or clearing (`stats` / `clear`) | Managing server cache |

---

## Environment Variables

Create a `.env` file in the `client/` directory:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | Yes (one of) | — | Google Gemini API key (preferred LLM) |
| `OPENAI_API_KEY` | Yes (one of) | — | OpenAI API key (fallback LLM) |
| `MCP_SERVER_URL` | No | `http://127.0.0.1:8000/mcp` | MCP server endpoint URL |
| `MCP_API_KEY` | No | — | API key for server auth (must match server's `MCP_API_KEY`) |

### Optional LangSmith Tracing

| Variable | Description |
|----------|-------------|
| `LANGCHAIN_API_KEY` | LangSmith API key for tracing |
| `LANGSMITH_TRACING` | Set to `true` to enable tracing |
| `LANGSMITH_ENDPOINT` | Custom tracing endpoint |
| `LANGCHAIN_PROJECT` | LangSmith project name |

### Example `.env` file

```dotenv
# LLM — set at least one
GOOGLE_API_KEY=AIza...your-gemini-key
# OPENAI_API_KEY=sk-...your-openai-key

# MCP server connection
MCP_SERVER_URL=http://127.0.0.1:8000/mcp
# MCP_API_KEY=your-server-api-key    # only if server has auth enabled

# Optional: LangSmith tracing
# LANGCHAIN_API_KEY=ls__...
# LANGSMITH_TRACING=true
# LANGCHAIN_PROJECT=mcp-rag-agent
```

---

## Example Conversations

### Querying a PDF Document

```
> What are the key findings in https://example.com/research-paper.pdf?

Agent thinking: I'll use retrieve_chunks to find the most relevant sections
about key findings.

[Tool call: retrieve_chunks(document_url="https://example.com/research-paper.pdf",
                            query="key findings", top_k=5)]

Based on the retrieved chunks, the key findings are:
1. Revenue increased by 15% year-over-year...
2. Customer satisfaction scores improved to 4.2/5...
3. ...
```

### Extracting Tables from a Spreadsheet

```
> Show me the data from https://example.com/quarterly-data.xlsx

Agent thinking: I'll extract the tables from this Excel file.

[Tool call: extract_xlsx_tables(document_url="https://example.com/quarterly-data.xlsx")]

The spreadsheet contains 3 sheets:
- Sheet "Revenue": 15 rows × 4 columns (Q1-Q4 revenue by region)
- Sheet "Costs": 12 rows × 3 columns (operating costs breakdown)
- ...
```

### Multi-Tool Interaction

```
> Summarise this document and tell me what language it's in:
  https://example.com/foreign-report.pdf

Agent thinking: I'll first extract the document content, then detect its language.

[Tool call: process_document(document_url="https://example.com/foreign-report.pdf")]
[Tool call: detect_language(text="<extracted content>")]

The document is written in French (language code: fr).

Summary: The report discusses...
```

---

## Architecture Details

### Agent Framework

- **Library:** LangGraph (`create_react_agent`)
- **Pattern:** ReAct — the LLM alternates between reasoning about what to do
  and acting by calling tools
- **Tool binding:** `langchain-mcp-adapters` converts MCP tool schemas into
  LangChain `BaseTool` instances automatically

### Connection Flow

```
1. agent.py starts
2. Loads .env (python-dotenv)
3. Selects LLM based on available API keys
4. Connects to MCP server via MultiServerMCPClient
5. Server returns list of available tools (MCP protocol)
6. langchain-mcp-adapters wraps each as a LangChain tool
7. create_react_agent(llm, tools) builds the agent
8. Agent enters REPL loop or processes one-shot query
9. On exit: MCP connection is closed cleanly (async context manager)
```

### Key Code Paths

```python
# LLM initialisation (simplified)
if os.getenv("GOOGLE_API_KEY"):
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
elif os.getenv("OPENAI_API_KEY"):
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

# MCP connection
async with MultiServerMCPClient({"rag-server": {
    "url": MCP_SERVER_URL,
    "transport": "streamable_http",
}}) as client:
    tools = client.get_tools()
    agent = create_react_agent(llm, tools)

    # Process query
    result = await agent.ainvoke({"messages": [("user", query)]})
```

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `langchain` | latest | Core agent framework |
| `langchain-google-genai` | latest | Gemini LLM integration |
| `langchain-mcp-adapters` | latest | MCP → LangChain tool bridge |
| `python-dotenv` | latest | `.env` file loading |

> **Note:** `langchain-openai` is needed if using OpenAI. Install it separately:
> `pip install langchain-openai`

---

## Troubleshooting

### Connection Refused

```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Cause:** MCP server is not running.
**Fix:** Start the server first:
```bash
cd ..
python -m mcp_server
```

### No API Key Set

```
Set GOOGLE_API_KEY or OPENAI_API_KEY in .env
```

**Cause:** Neither LLM key is configured.
**Fix:** Edit `client/.env` and add at least one API key.

### Authentication Error from Server

```json
{"error": "Authentication required", "code": "AUTH_ERROR"}
```

**Cause:** Server has `MCP_API_KEY` set but client isn't sending it.
**Fix:** Add `MCP_API_KEY=<same-key>` to `client/.env`.

### Rate Limited

```json
{"error": "Rate limit exceeded", "code": "RATE_LIMITED"}
```

**Cause:** Too many requests per minute.
**Fix:** Wait and retry, or increase `MCP_RATE_LIMIT_RPM` on the server.

### Tool Timeout

```json
{"error": "Tool execution timed out", "code": "TIMEOUT"}
```

**Cause:** Document is very large or server is under heavy load.
**Fix:** Try a smaller document, or increase `MCP_REQUEST_TIMEOUT` on the server.

### Server URL Mismatch

If using a non-default server address, ensure `MCP_SERVER_URL` in your `.env`
matches the server's actual address:

```dotenv
# If server runs on port 9000:
MCP_SERVER_URL=http://127.0.0.1:9000/mcp

# If server runs on a different host:
MCP_SERVER_URL=http://192.168.1.100:8000/mcp
```

---

## License

MIT — same as the server.
