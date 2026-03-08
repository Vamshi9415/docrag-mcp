# RAG + FastMCP System — Interview Questions & Answers

> A comprehensive deep-dive into every architectural decision, protocol, algorithm, and production pattern implemented in this RAG Document Server. Every answer references the **actual code** in this repository.
>
> **This server is a pure MCP (Model Context Protocol) server** — it uses FastMCP with two transports: **streamable-http** (for network AI agents) and **stdio** (for desktop AI clients). There is no REST API layer.

---

## Table of Contents

1. [MCP Protocol and FastMCP](#1-mcp-protocol-and-fastmcp)
2. [Transport Mechanisms](#2-transport-mechanisms)
3. [Why Two Transports](#3-why-two-transports)
4. [Rate Limiting](#4-rate-limiting)
5. [Document Processing and Caching](#5-document-processing-and-caching)
6. [Indexing and Retrieval](#6-indexing-and-retrieval)
7. [Three-Layer Cache Architecture](#7-three-layer-cache-architecture)
8. [FAISS Disk Persistence](#8-faiss-disk-persistence)
9. [File Re-Index Detection](#9-file-re-index-detection)
10. [Concurrency and Performance Optimizations](#10-concurrency-and-performance-optimizations)
11. [Advanced Retrieval Optimizations](#11-advanced-retrieval-optimizations)
12. [Connection Management](#12-connection-management)
13. [System Architecture Questions](#13-system-architecture-questions)
14. [Production-Level Questions](#14-production-level-questions)

---

## 1. MCP Protocol and FastMCP

### Q: What is the MCP (Model Context Protocol)?

MCP (Model Context Protocol) is an **open standard** developed by Anthropic for connecting AI assistants (LLMs) to external tools, data sources, and services. Think of it as **USB-C for AI** — a universal plug that lets any AI model talk to any tool.

**Key concept:** Instead of every AI assistant (Claude, Copilot, GPT) implementing custom integrations for every tool, MCP provides a single standardised interface:

```
┌──────────────┐     MCP Protocol      ┌──────────────┐
│  AI Agent    │ ◄────────────────────► │  MCP Server  │
│  (Claude,    │   JSON-RPC over        │  (Our RAG    │
│   Copilot)   │   stdio / HTTP         │   Server)    │
└──────────────┘                        └──────────────┘
```

In our system, the MCP server exposes 13 tools (process, chunk, retrieve, extract, etc.) that any MCP-compatible AI agent can call without writing HTTP glue code.

### Q: Why was MCP introduced and what problem does it solve?

**The problem before MCP:**
- Each AI assistant needed custom integrations for each tool
- Different APIs had different auth mechanisms, error formats, and response structures
- AI agents couldn't dynamically discover what tools are available
- Session management, streaming, and tool orchestration were ad-hoc

**What MCP solves:**
1. **Tool Discovery** — An MCP client connects to the server and asks "what tools do you have?" The server responds with a structured list including names, descriptions, and parameter schemas. Our server returns all 13 tools + 2 resources.
2. **Standardised Invocation** — The AI agent says "call `retrieve_chunks` with `{document_url: ..., query: ...}`" using JSON-RPC. No custom HTTP client code needed.
3. **Type Safety** — Tool parameters and return types are described in JSON Schema, so the AI knows exactly what arguments each tool expects.
4. **Protocol-Level Features** — Timeouts, cancellation, progress reporting, and streaming are built into the protocol.
5. **Client-Server Decoupling** — Our server doesn't know or care whether it's talking to Claude, Copilot, or a LangChain agent. They all speak MCP.

### Q: How does MCP enable communication between LLMs and external tools?

MCP uses **JSON-RPC 2.0** over a transport layer (stdio or HTTP). The flow:

1. **Initialisation** — The MCP client (AI agent) connects and discovers tools:
   ```json
   → {"method": "tools/list"}
   ← {"tools": [{"name": "retrieve_chunks", "description": "...", "inputSchema": {...}}]}
   ```

2. **Tool Call** — The LLM decides it needs to call a tool:
   ```json
   → {"method": "tools/call", "params": {"name": "retrieve_chunks", "arguments": {"document_url": "...", "query": "revenue"}}}
   ← {"content": [{"type": "text", "text": "{\"results\": [...], \"total_chunks_indexed\": 42}"}]}
   ```

3. **Resource Read** — The client can read server-provided resources:
   ```json
   → {"method": "resources/read", "params": {"uri": "rag://supported-formats"}}
   ← {"contents": [{"text": "Supported formats: PDF, DOCX, ..."}]}
   ```

In our implementation:
- The `@mcp.tool()` decorator in `mcp_server/tools/query.py`, `extract.py`, and `utility.py` registers each function as an MCP tool.
- The `@mcp.resource()` decorator in `mcp_server/resources/__init__.py` registers read-only resources.
- FastMCP handles the JSON-RPC serialization, tool dispatch, and error reporting automatically.

### Q: What is FastMCP?

FastMCP is the **official Python SDK** for building MCP servers. It's to MCP what a framework is to raw HTTP — a high-level abstraction that handles the protocol boilerplate so you write only business logic.

Our server creates a FastMCP instance in `mcp_server/server.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "RAG Document Server",
    instructions=(
        "Pure deterministic MCP tool server for document processing. "
        "Supports PDF, DOCX, PPTX, XLSX, CSV, TXT, HTML, and image (OCR). "
        "This server does NOT contain any LLM or agentic logic. ..."
    ),
    lifespan=_lifespan,
)
```

**The `instructions` field** is sent to every MCP client on connection — it tells the AI agent what this server does and how to use its tools. This is like a system prompt embedded in the protocol.

### Q: How does FastMCP simplify MCP server implementation?

Without FastMCP, you'd have to:
1. Implement JSON-RPC request/response parsing manually
2. Handle `tools/list`, `tools/call`, `resources/list`, `resources/read` methods
3. Manage session state, transport negotiation, capability exchange
4. Serialise Python return values to MCP content format
5. Handle errors, cancellation, and timeout at the protocol level

With FastMCP, our entire tool registration is one decorator:

```python
@mcp.tool()
@guarded(timeout=300)
async def retrieve_chunks(document_url: str, query: str, top_k: int = 5) -> dict:
    # ... business logic ...
    return {"results": [...], "total_chunks_indexed": 42}
```

FastMCP automatically:
- Extracts parameter names and types from the Python function signature
- Generates the JSON Schema for the tool's `inputSchema`
- Uses the docstring as the tool description (shown to AI agents)
- Serialises the return `dict` to MCP `TextContent`
- Reports exceptions as MCP error responses

### Q: What are MCP tools, resources, and prompts?

| Concept | Description | Our Implementation |
|---------|-------------|-------------------|
| **Tools** | Functions the AI can call with arguments + get results | 13 tools: `process_document`, `chunk_document`, `retrieve_chunks`, 6× `extract_*`, `query_spreadsheet`, `detect_language`, `get_system_health`, `manage_cache` |
| **Resources** | Read-only data the AI can access (like config files) | 2 resources: `rag://supported-formats`, `rag://tool-descriptions` |
| **Prompts** | Pre-defined prompt templates (not used in our project) | Not used — our server is a pure tool server, not an LLM wrapper |

**Tools** (`mcp_server/tools/`):
```python
@mcp.tool()
async def retrieve_chunks(document_url: str, query: str, top_k: int = 5) -> dict:
    """Download and process a document, build a FAISS vector index, and return top-K chunks."""
```

**Resources** (`mcp_server/resources/__init__.py`):
```python
@mcp.resource("rag://supported-formats")
def supported_formats() -> str:
    """List of all supported document formats."""
    return "Documents: PDF, DOCX, PPTX, TXT, HTML\nTables: XLSX, CSV\nImages: PNG, JPEG"
```

Resources are useful because they give the AI agent context about the server's capabilities **before** it starts making tool calls.

### Q: How does an MCP client communicate with an MCP server?

The client (AI agent) communicates via one of two transports:

**1. Streamable-HTTP transport** (our default):
```
Client                                 Server
  │                                      │
  │─── POST /mcp (initialise) ─────────►│
  │◄── 200 {capabilities, tools} ───────│
  │                                      │
  │─── POST /mcp (tools/call) ─────────►│
  │◄── 200 {result} ────────────────────│
```

**2. STDIO transport** (for local AI desktop clients):
```
Client          stdin/stdout           Server
  │─── JSON-RPC line ──────────────────►│
  │◄── JSON-RPC response ──────────────│
```

Our client implementation in `client/agent.py` uses the streamable-HTTP transport:

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "rag-server": {
        "url": "http://127.0.0.1:8000/mcp",
        "transport": "streamable_http",
    }
})
tools = await client.get_tools()  # discovers all 13 tools
```

The `langchain_mcp_adapters` library converts MCP tools into LangChain-compatible tools, so the AI agent (Gemini/GPT-4o-mini) can call them via function calling.

---

## 2. Transport Mechanisms

### Q: What are the two transport mechanisms used in the system?

Our system supports **two MCP transports**, selected via CLI:

```bash
python -m mcp_server --transport streamable-http   # default — MCP over HTTP
python -m mcp_server --transport stdio              # MCP over stdin/stdout
```

| Transport | Protocol | Port | Endpoint | Use Case |
|-----------|----------|------|----------|----------|
| **streamable-http** | MCP (JSON-RPC over HTTP) | 8000 | `/mcp` | AI agents over network (Claude, Copilot, LangChain) |
| **stdio** | MCP (JSON-RPC over stdin/stdout) | N/A | N/A | Desktop AI apps (Claude Desktop, VS Code) |

Both transports speak the **same MCP protocol** — the only difference is the physical channel. Tools, resources, rate-limiting, and the entire service layer are identical regardless of transport.

Implementation in `mcp_server/__main__.py`:

```python
if args.transport == "streamable-http":
    uvicorn.run("mcp_server._asgi:create_app", factory=True, ...)
else:  # stdio
    mcp_server = create_server(args.host, args.port)
    mcp_server.run(transport="stdio")
```

### Q: What is STDIO transport and how does it work?

STDIO (Standard Input/Output) transport sends MCP JSON-RPC messages through the process's standard input and output streams.

**How it works:**
1. The AI client (e.g., Claude Desktop) **spawns our server as a child process**: `python -m mcp_server --transport stdio`
2. The client writes JSON-RPC requests to the server's **stdin**
3. The server writes JSON-RPC responses to its **stdout**
4. All logging goes to **stderr** (critical! — otherwise log messages corrupt the protocol)

```
Claude Desktop                    MCP Server Process
     │                                    │
     │─── stdin: {"method":"tools/list"}─►│
     │◄── stdout: {"tools":[...]}────────│
     │                                    │
     │─── stdin: {"method":"tools/call", ─►│
     │     "params":{"name":"retrieve_chunks",...}}
     │◄── stdout: {"content":[...]}──────│
```

**Why stderr for logging is critical** — Our `mcp_server/core/logging.py` sends all logs to stderr:
```python
console_handler = logging.StreamHandler(sys.stderr)  # NOT sys.stdout
```
If we logged to stdout, log lines like `{"ts":"...", "level":"INFO", "msg":"download.success"}` would be mixed with MCP responses, corrupting the protocol.

### Q: What is streamable-HTTP transport and how does it work?

The **streamable-http** transport runs MCP over standard HTTP POST requests. This is the default and most flexible transport.

**Architecture stack** (from `mcp_server/_asgi.py`):

```
               HTTP Request
                    │
                    ▼
            ┌───────────────┐
            │   MCPRouter   │  ← handles GET /health, GET /info
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │   FastMCP     │  ← processes POST /mcp (JSON-RPC)
            │ streamable_   │
            │ http_app()    │
            └───────────────┘
```

**Detailed request flow:**
1. Client sends `POST /mcp` with JSON-RPC body
2. `MCPRouter` checks if it's `/health` or `/info` (custom endpoints) — if not, forwards to FastMCP
3. FastMCP parses the JSON-RPC request, dispatches to the correct tool, and returns the result

Implementation in `mcp_server/_asgi.py`:
```python
def create_app():
    from mcp_server.server import mcp
    from mcp_server.middleware.guards import MCPRouter
    return MCPRouter(mcp.streamable_http_app())
```

### Q: What is the detailed request-response flow for STDIO transport?

```
1. Claude Desktop spawns: python -m mcp_server --transport stdio
2. Server starts → loads ML models → prints ready status to stderr

3. Client → stdin:
   {"jsonrpc":"2.0","id":1,"method":"initialize",
    "params":{"capabilities":{}}}

4. Server → stdout:
   {"jsonrpc":"2.0","id":1,"result":{"capabilities":{"tools":{}}}}

5. Client → stdin:
   {"jsonrpc":"2.0","id":2,"method":"tools/list"}

6. Server → stdout:
   {"jsonrpc":"2.0","id":2,"result":{"tools":[
     {"name":"retrieve_chunks","description":"...","inputSchema":{...}},
     {"name":"process_document","description":"...","inputSchema":{...}},
     ...13 tools...
   ]}}

7. Client → stdin:
   {"jsonrpc":"2.0","id":3,"method":"tools/call",
    "params":{"name":"retrieve_chunks",
              "arguments":{"document_url":"https://...","query":"revenue"}}}

8. Server internally:
   a. @guarded decorator → check_rate_limit()
   b. download(url) → cache check → httpx.get(url) → cache store
   c. TargetedDocumentProcessor.process_document()
   d. AdaptiveChunkingStrategy.create_chunks()
   e. run_in_gpu_pool(EnhancedRetriever, ...) — FAISS build
   f. run_in_gpu_pool(retriever.retrieve, query, top_k) — vector search + rerank

9. Server → stdout:
   {"jsonrpc":"2.0","id":3,"result":{"content":[
     {"type":"text","text":"{\"results\":[...],\"total_chunks_indexed\":42}"}
   ]}}
```

### Q: What is the detailed request-response flow for HTTP transport?

```
1. Server starts: python -m mcp_server --transport streamable-http
   → Uvicorn binds to 0.0.0.0:8000
   → ML models loaded during lifespan startup

2. Client → POST http://localhost:8000/mcp
   Headers: Content-Type: application/json
   Body: {"jsonrpc":"2.0","id":1,"method":"tools/call",
          "params":{"name":"retrieve_chunks",
                    "arguments":{"document_url":"https://...","query":"revenue"}}}

3. ASGI Stack:
   a. MCPRouter.__call__() → path is "/mcp", not "/health" or "/info" → forwards to FastMCP
   b. FastMCP.streamable_http_app() → parses JSON-RPC → calls retrieve_chunks()

4. Tool execution (same as STDIO step 8 above):
   @guarded → rate limit → timeout → download → process → chunk → embed → retrieve

5. Server → HTTP 200
   Headers: Content-Type: application/json
   Body: {"jsonrpc":"2.0","id":1,"result":{"content":[
     {"type":"text","text":"{\"results\":[...],\"total_chunks_indexed\":42}"}
   ]}}
```

### Q: How do these transports operate independently in the architecture?

The two transports are **completely independent launch modes** — they never run simultaneously in the same process:

```
Transport Layer (only ONE active at a time)
    │
    ├─ streamable-http → MCPRouter → FastMCP → @mcp.tool() functions
    │
    └─ stdio → FastMCP.run(transport="stdio") → @mcp.tool() functions
    │
    ▼
Shared Service Layer (identical for both transports)
    ├─ services/downloader.py    — HTTP download + retry + cache
    ├─ services/cache.py         — 3-layer TTL cache
    ├─ services/chunking.py      — Adaptive chunking
    ├─ services/retrieval.py     — FAISS + reranking
    ├─ services/language.py      — Language detection
    ├─ processors/*.py           — PDF, DOCX, PPTX, XLSX, Image, URL
    └─ core/models.py            — Embedding + reranker models
```

The service layer doesn't know which transport is active. Whether a tool call comes via HTTP or stdio, the same `download()`, `AdaptiveChunkingStrategy`, `EnhancedRetriever` functions process it — because both transports call the **same `@mcp.tool()` functions**.

---

## 3. Why Two Transports

### Q: Why are two transport mechanisms implemented instead of just one?

Different AI clients have fundamentally different communication needs:

1. **Network AI agents** (Claude API, LangChain, custom agents) need **streamable-http** — they connect over a network, send HTTP requests, and expect HTTP responses. This supports multi-client, load balancing, and Docker/Kubernetes deployments.

2. **Desktop AI apps** (Claude Desktop, VS Code Copilot) need **stdio** — they spawn the server as a local subprocess. No network port is opened, and communication is process-local.

**Real-world analogy:** A USB peripheral supports both USB-A and USB-C because different computers have different ports. The peripheral is the same; the connector adapts.

### Q: In which scenarios is STDIO transport preferred?

- **Claude Desktop** — Configured in `claude_desktop_config.json` to spawn the server as a local process
- **VS Code Copilot** — The MCP extension talks to tools via stdio
- **Security-sensitive environments** — No network port is opened; communication is process-local. No risk of external access
- **Single-user local development** — The server is isolated to the current user's process tree
- **Air-gapped systems** — No network required at all

### Q: When is streamable-HTTP transport more useful?

- **Remote MCP clients** — AI agents running on a different machine or in the cloud
- **Multi-client** — Multiple AI agents sharing one server instance
- **Load-balanced deployments** — Multiple server instances behind a reverse proxy
- **Docker / Kubernetes** — Container-to-container communication over HTTP
- **Our client agent** (`client/agent.py`) — Connects via `http://127.0.0.1:8000/mcp`
- **CI/CD pipelines** — Automated tools connecting over HTTP

### Q: What are the advantages and disadvantages of each transport?

| Transport | Advantages | Disadvantages |
|-----------|-----------|---------------|
| **streamable-http** | Network-accessible; multi-client; load-balanceable; Docker/K8s friendly | Requires HTTP server (Uvicorn); more overhead per request; needs open port |
| **stdio** | Zero network setup; process-isolated; lowest latency; inherently secure | Single-client only; can't scale horizontally; requires process spawn |

### Q: How does transport abstraction improve system flexibility?

The architecture is designed with a **clean layered separation**:

```
Layer 1: TRANSPORT           ← streamable-http | stdio
Layer 2: MIDDLEWARE           ← rate-limit → validation → logging → timeout
Layer 3: TOOLS (13 MCP)      ← process, chunk, retrieve, extract×6, query, detect, health, cache
Layer 4: SERVICES             ← downloader, cache (3-layer), chunking, retrieval, language
Layer 5: PROCESSORS           ← PDF, DOCX, PPTX, XLSX/CSV, Image/OCR, HTML/TXT, URL
Layer 6: ML MODELS            ← MiniLM-L6-v2, BGE-small-en, ms-marco reranker
```

Layers 2–6 are **transport-agnostic**. Adding a new transport (e.g., WebSocket, SSE) requires only adding a new Layer 1 adapter — zero changes to tools, services, or processors.

---

## 4. Rate Limiting

### Q: What is rate limiting and why is it important?

Rate limiting controls **how many requests a client can make per unit of time**. It's critical for:

1. **Preventing abuse** — A single client can't overwhelm the server
2. **Fair resource sharing** — Multiple clients get equitable access
3. **Cost control** — Embedding models and GPU compute are expensive
4. **DoS protection** — Limits the damage from denial-of-service attempts

In our system, rate limiting protects the expensive ML operations (FAISS build, embedding generation, cross-encoder reranking) that consume significant CPU/GPU resources.

### Q: What algorithm is used for rate limiting in the system?

We use the **Token Bucket** algorithm, implemented in `mcp_server/middleware/guards.py`:

```python
class _TokenBucket:
    """Thread-safe token-bucket rate limiter."""

    def __init__(self, rpm: int):
        self.capacity = float(rpm)       # max tokens (burst capacity)
        self.tokens = float(rpm)         # current tokens (starts full)
        self.refill_rate = rpm / 60.0    # tokens per second
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(
                self.capacity,
                self.tokens + (now - self.last_refill) * self.refill_rate,
            )
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False
```

**How it works (with default 60 RPM):**
- Bucket starts with 60 tokens (full capacity)
- Each request consumes 1 token
- Tokens refill at 1 per second (60/60)
- If 60 requests arrive in 1 second: all succeed (burst), then bucket is empty
- Next request must wait ~1 second for a token to refill
- If requests arrive at 1/second: bucket stays full, no rejections

### Q: What is the difference between Token Bucket and Leaky Bucket algorithms?

| Feature | Token Bucket (our choice) | Leaky Bucket |
|---------|--------------------------|--------------|
| **Burst handling** | Allows bursts up to bucket capacity | No bursts — constant drain rate |
| **Analogy** | Water filling a bucket; each request takes a cup | Water dripping through a hole at fixed rate |
| **Behaviour** | 60 RPM bucket: 60 requests in 1 second, then wait 60s | 60 RPM: exactly 1 request per second, always |
| **Peak throughput** | Can be higher than average rate (burst) | Never exceeds the constant rate |
| **Why we chose it** | Real workloads are bursty — an agent makes 5 tool calls in a row, then pauses | Too strict for interactive AI agents |

### Q: How is rate limiting implemented?

Rate limiting runs at **two levels**:

**1. Per-user bucket** — Each caller identifier (or "anonymous" by default) gets its own token bucket:
```python
_user_buckets: dict[str, _TokenBucket] = {}

def _get_user_bucket(api_key: str) -> _TokenBucket:
    with _user_buckets_lock:
        bucket = _user_buckets.get(api_key)
        if bucket is not None:
            return bucket
        # Evict oldest bucket if at capacity (FIFO eviction)
        if len(_user_buckets) >= _MAX_USER_BUCKETS:  # 1000 max
            oldest_key = next(iter(_user_buckets))
            del _user_buckets[oldest_key]
        bucket = _TokenBucket(security_config.rate_limit_rpm)
        _user_buckets[api_key] = bucket
        return bucket
```

**2. Global server bucket** — A server-wide bucket at 5× the per-user rate:
```python
_global_bucket = _TokenBucket(security_config.rate_limit_rpm * 5)  # 300 RPM total
```

**Why two levels?**
- Per-user: one client can't hog all resources
- Global: even if 100 valid users each send 60 RPM (6000 total), the server is capped at 300 RPM

**Memory safety:** A maximum of 1000 user buckets are tracked. When the limit is reached, the oldest bucket is evicted (FIFO). This prevents memory exhaustion from API key enumeration attacks.

### Q: Where in the request pipeline is rate limiting enforced?

```
Incoming MCP Tool Call
     │
     ▼
[1] Rate Limiting (check_rate_limit)              ← reject if exceeded
     │
     ▼
[2] Input Validation (validate_url / validate_text)
     │
     ▼
[3] Timeout Enforcement (asyncio.wait_for)
     │
     ▼
[4] Tool Execution (business logic)
     │
     ▼
[5] Response Logging
```

This pipeline is implemented in the `@guarded` decorator (`mcp_server/middleware/__init__.py`) which wraps every `@mcp.tool()` function.

### Q: How does the system handle requests that exceed the rate limit?

```python
def check_rate_limit(tool_name: str = "", api_key: str = "") -> None:
    user_key = api_key or "anonymous"
    user_bucket = _get_user_bucket(user_key)

    if not user_bucket.consume():
        raise RateLimitError(
            f"Rate limit exceeded ({security_config.rate_limit_rpm} req/min). Try again shortly."
        )

    if not _global_bucket.consume():
        # IMPORTANT: Refund the user token since the request won't proceed
        with user_bucket._lock:
            user_bucket.tokens = min(user_bucket.capacity, user_bucket.tokens + 1.0)
        raise RateLimitError("Server is under heavy load. Try again shortly.")
```

**When per-user limit is exceeded:**
- `RateLimitError` is raised → the `@guarded` decorator catches it and returns an error dict to the MCP client

**When global limit is exceeded:**
- The user's token is **refunded** (they didn't cause the global overload)
- A different error message is returned ("server is under heavy load")

---

## 5. Document Processing and Caching

### Q: How are documents processed when a PDF is uploaded?

Here's the complete flow when a PDF hits `retrieve_chunks`:

```
1. URL validation: validate_url(document_url)
   → regex checks for http/https, length ≤ 2048

2. Hash computation: url_hash = SHA-256(url)[:16]
   → "8cbafb4c8dc875d3" (used as cache key and FAISS directory name)

3. Document cache check: get_cached_document(url_hash)
   → HIT? Skip to step 7
   → MISS? Continue to step 4

4. Download: await download(document_url)
   → download_cache check → HIT? Return cached bytes
   → MISS? httpx.get(url) → 3× retry → cache the bytes

5. Type detection: detect_document_type(url)
   → Parses URL extension: ".pdf" → "pdf"

6. Processing: TargetedDocumentProcessor.process_document(bytes, "pdf", url, request_id)
   → Dispatches to EnhancedPDFProcessor.extract_pdf_content()
   → PyMuPDF (fitz) opens the PDF from bytes
   → Iterates pages → extracts text blocks with layout preservation
   → Extracts URLs from text via regex
   → Detects language via 3-round sampling
   → Returns ProcessedDocument(content, metadata, tables, images, urls, language)

7. Cache store: put_cached_document(url_hash, processed)
   → Stored in document_cache (TTL: 30 min, max 50 entries)
```

### Q: What preprocessing steps are applied to documents?

Every document goes through these preprocessing steps:

1. **Text sanitization** — Remove null bytes and control characters:
   ```python
   def _sanitize_text(text: str) -> str:
       text = text.replace("\x00", "")
       text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
       return text
   ```
   This prevents JSON serialization failures and MCP protocol corruption.

2. **URL extraction** — `URLExtractor` scans extracted text for URLs using regex:
   ```
   https?://[^\s<>"']+
   www\.[^\s<>"']+\.[^\s<>"']+
   ```

3. **Language detection** — `detect_language_robust()` uses 3-round majority-vote:
   ```python
   attempts = [detect(text[:5000]) for _ in range(3)]
   return Counter(attempts).most_common(1)[0][0]
   ```

4. **Content truncation** — Output content is capped at 50,000 characters to prevent memory issues in MCP transmission.

### Q: How is text extracted from PDFs?

We use **PyMuPDF (fitz)** for PDF text extraction in `mcp_server/processors/pdf.py`:

```python
class EnhancedPDFProcessor:
    @staticmethod
    def extract_pdf_content(file_content: bytes) -> str:
        doc = fitz.open(stream=file_content, filetype="pdf")
        pages = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict", sort=True).get("blocks", [])
            page_lines = []

            for block in blocks:
                if block.get("type") == 0:  # text block (not image)
                    for line in block.get("lines", []):
                        spans_text = " ".join(
                            span.get("text", "") for span in line.get("spans", [])
                        )
                        if spans_text.strip():
                            page_lines.append(spans_text.strip())

            if page_lines:
                pages.append(f"--- Page {page_num + 1} ---\n" + "\n".join(page_lines))

        doc.close()
        return "\n\n".join(pages)
```

**Why `get_text("dict")` instead of `get_text()`?**
- `get_text()` returns plain text with no structural information
- `get_text("dict")` returns a dictionary with blocks, lines, and spans — preserving the document's visual layout
- `sort=True` reorders blocks by their position on the page (top-to-bottom, left-to-right)
- Page markers (`--- Page N ---`) help the AI agent reference specific pages

**Fallback:** If dict-based extraction fails, we fall back to plain `page.get_text()`.

### Q: How are images or tables handled during extraction?

**Images:** For documents with embedded images (or standalone image files), we use **OCR via pytesseract** in `mcp_server/processors/image.py`:
```python
if doc_type == "image":
    if OCR_AVAILABLE:
        images = await ImageOCRProcessor.process_image_file(file_content, file_path, request_id)
        text = "\n".join(img.ocr_text for img in images if img.ocr_text)
```
OCR is optional — if `pytesseract` is not installed, the feature degrades gracefully (the `OCR_AVAILABLE` flag is set at import time).

**Tables:** For XLSX/CSV files, `EnhancedXLSXTableExtractor` uses **pandas** for extraction:
- Reads each sheet with `pd.ExcelFile.parse()`
- Detects the header row by scoring uniqueness, text ratio, and column coverage
- Formats tables with dimensions, headers, data analysis, and cross-sheet relationships
- Returns `ExtractedTable` dataclass with content, type, location, and metadata

### Q: What is semantic chunking and why is it used?

Semantic chunking splits a document into smaller pieces (**chunks**) that are meaningful for retrieval. Instead of splitting at arbitrary character boundaries, we split at natural content boundaries.

**Why chunking is necessary:**
- Embedding models have a **context window** (typically 512 tokens for MiniLM)
- A 100-page PDF has ~300,000 characters — far too large for a single embedding
- Without chunking, vector search would compare the query against the entire document (poor precision)
- With chunking, the query is compared against 200 small, focused chunks — the most relevant ones are returned

Our `AdaptiveChunkingStrategy` (Section 13) does this with type-aware sizing, overlap, and importance scoring.

---

## 6. Indexing and Retrieval

### Q: What is document indexing in a RAG pipeline?

Document indexing is the process of converting text chunks into **vector embeddings** and storing them in a **vector database** (FAISS) for fast similarity search.

**In our system, indexing happens in `retrieve_chunks`:**

```
Document Text → Chunks → Embeddings → FAISS Index
                   │          │            │
    AdaptiveChunkingStrategy  │     FAISS.from_documents()
                         HuggingFaceEmbeddings
```

Each chunk is converted to a 384-dimensional vector that captures its semantic meaning. Semantically similar text produces vectors that are close in this high-dimensional space.

### Q: How are document chunks converted into embeddings?

We use **HuggingFace sentence transformers** to generate embeddings:

```python
# mcp_server/core/models.py
from langchain_huggingface import HuggingFaceEmbeddings

_embeddings_fast = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={"device": DEVICE},          # "cuda", "mps", or "cpu"
    encode_kwargs={
        "normalize_embeddings": True,          # L2 normalize for cosine similarity
        "batch_size": 32,                      # embed 32 chunks at once
    },
)
```

**The embedding process:**
1. Each chunk text (e.g., "Revenue increased by 15% in Q3 2025") is tokenized
2. Tokens are passed through the MiniLM-L6-v2 transformer model
3. The model outputs a 384-dimensional vector: `[0.023, -0.145, 0.087, ...]`
4. The vector is L2-normalized (unit length) for cosine similarity
5. All chunk vectors are collected and indexed in FAISS

**Model selection logic** (adaptive):
```python
# mcp_server/tools/query.py — in retrieve_chunks
emb_model = "fast" if len(chunks) <= 50 else "accurate"
emb = get_embeddings_fast() if len(chunks) <= 50 else get_embeddings_accurate()
```
- ≤50 chunks: Use **MiniLM-L6-v2** (fast, 80ms/batch) — speed matters more for small docs
- >50 chunks: Use **BGE-small-en-v1.5** (accurate, 150ms/batch) — quality matters for larger docs

### Q: How does FAISS index embeddings?

FAISS (Facebook AI Similarity Search) builds an index over the embedding vectors:

```python
# mcp_server/services/retrieval.py
from langchain_community.vectorstores import FAISS

class EnhancedRetriever:
    def __init__(self, embeddings, chunks, embedding_model_name="fast"):
        self.vectorstore = FAISS.from_documents(chunks, embeddings)
```

**What `FAISS.from_documents()` does:**
1. Takes the list of `Document` objects (each with `page_content` and `metadata`)
2. Passes all `page_content` values through the embedding model (batched)
3. Builds a FAISS `IndexFlatIP` (inner product index for cosine similarity on normalized vectors)
4. Stores the document→vector mapping in an internal docstore

**At retrieval time:**
```python
candidates = self.vectorstore.similarity_search(query, k=min(top_k * 3, 20))
```
1. The query text is embedded using the same model
2. FAISS performs cosine similarity search across all indexed vectors
3. The top `k` most similar chunks are returned
4. These candidates are then **reranked** by the cross-encoder for higher precision

### Q: How does the system identify previously indexed documents?

Every document URL is hashed to a deterministic 16-character identifier:

```python
url_hash = hashlib.sha256(document_url.encode()).hexdigest()[:16]
# Example: "https://example.com/report.pdf" → "8cbafb4c8dc875d3"
```

This hash is used as:
1. **Memory cache key** — `retriever_cache.get("8cbafb4c8dc875d3")`
2. **Disk directory name** — `faiss_indexes/8cbafb4c8dc875d3/index.faiss`
3. **Document cache key** — `document_cache.get("8cbafb4c8dc875d3")`

Before building a new FAISS index, the system checks both memory and disk:
```python
retriever, source = get_retriever_with_disk_fallback(url_hash)
# Returns: (retriever_object, "memory") — fast path
#      or: (retriever_object, "disk")   — loaded from faiss_indexes/
#      or: (None, None)                 — must build fresh
```

### Q: How does the system prevent duplicate indexing?

Three mechanisms prevent duplicate work:

**1. Memory cache** — The fastest check:
```python
cached = retriever_cache.get(url_hash)
if cached is not None:
    return cached, "memory"
```

**2. Disk persistence** — Survives server restarts:
```python
loaded = EnhancedRetriever.load_from_disk(url_hash, embeddings)
if loaded is not None:
    retriever_cache.put(url_hash, loaded)  # promote to memory
    return loaded, "disk"
```

**3. Async lock coalescing** — Prevents concurrent duplicate builds:
```python
# If 10 requests for the same URL arrive simultaneously,
# only the FIRST builds the index. The other 9 wait.
async def coalesced_build(url_hash, build_fn):
    lock = await _get_build_lock(url_hash)
    async with lock:
        return await build_fn()
```

### Q: What metadata is stored along with embeddings?

Each chunk carries rich metadata:

```python
# Set during chunking (mcp_server/services/chunking.py)
doc.metadata = {
    "chunk_index": 0,           # position in document
    "total_chunks": 42,         # total chunk count
    "importance_score": 0.85,   # 0.0–1.0 (headings, numbers, keywords boost score)
    "content_type": "text",     # "text", "table", "list", or "heading"
    "doc_type": "pdf",          # source document format
}
```

Additionally, FAISS saves a JSON sidecar file (`chunks_meta.json`):
```json
[
  {"page_content": "Revenue increased by 15%...", "metadata": {"chunk_index": 0, ...}},
  {"page_content": "Operating expenses...", "metadata": {"chunk_index": 1, ...}}
]
```

And an `index_meta.json` recording which embedding model was used:
```json
{"embedding_model": "fast"}
```

---

## 7. Three-Layer Cache Architecture

### Q: What are the three layers of caching in the system?

```
┌─────────────────────────────────────────────────────────┐
│                THREE-LAYER CACHE                         │
│                                                          │
│  Layer 1: DOWNLOAD CACHE                                 │
│  ├─ Key: document URL                                    │
│  ├─ Value: raw bytes (PDF/DOCX/XLSX content)             │
│  ├─ Max: 50 entries, 500 MB total                        │
│  └─ TTL: 30 minutes                                      │
│                                                          │
│  Layer 2: DOCUMENT CACHE                                 │
│  ├─ Key: url_hash (SHA-256[:16])                         │
│  ├─ Value: ProcessedDocument (text, tables, metadata)    │
│  ├─ Max: 50 entries                                      │
│  └─ TTL: 30 minutes                                      │
│                                                          │
│  Layer 3: RETRIEVER CACHE                                │
│  ├─ Key: url_hash                                        │
│  ├─ Value: EnhancedRetriever (FAISS index + chunks)      │
│  ├─ Max: 20 entries                                      │
│  ├─ TTL: 30 minutes (memory)                             │
│  └─ Fallback: disk persistence (faiss_indexes/ — no TTL) │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

Implementation in `mcp_server/services/cache.py`:

```python
download_cache = _TTLCache(max_entries=50, ttl=1800, name="download", max_bytes=500*1024*1024)
document_cache = _TTLCache(max_entries=50, ttl=1800, name="document")
retriever_cache = _TTLCache(max_entries=20, ttl=1800, name="retriever")
```

### Q: Why is a multi-layer caching architecture useful?

Each layer caches a different stage of the processing pipeline. This means a cache hit at ANY layer skips everything below it:

```
Request: "retrieve_chunks for https://example.com/report.pdf"

→ Check retriever_cache (Layer 3)  ← HIT: skip download, processing, chunking, embedding
→ Check document_cache (Layer 2)   ← HIT: skip download, processing (still need chunking + embedding)
→ Check download_cache (Layer 1)   ← HIT: skip HTTP download (still need processing, chunking, embedding)
→ MISS at all layers               ← Full pipeline: download → process → chunk → embed → index
```

**Performance impact:**

| Cache Hit | Time Saved | What's Skipped |
|-----------|-----------|----------------|
| Retriever (L3 memory) | ~5–15 seconds | Download + process + chunk + embed |
| Retriever (L3 disk) | ~3–10 seconds | Download + process + chunk + embed (FAISS load from disk ~1s) |
| Document (L2) | ~2–5 seconds | Download + process |
| Download (L1) | ~1–5 seconds | HTTP download |

### Q: How does disk persistence work with FAISS?

When a new FAISS index is built, it's persisted to disk immediately:

```python
# mcp_server/services/cache.py
def put_retriever_with_disk(url_hash: str, retriever) -> None:
    retriever_cache.put(url_hash, retriever)   # memory cache
    retriever.save_to_disk(url_hash)           # disk persistence
```

The disk structure for each indexed document:
```
faiss_indexes/
└── 8cbafb4c8dc875d3/          # url_hash directory
    ├── index.faiss             # FAISS binary index (vectors)
    ├── index.pkl               # LangChain docstore pickle
    ├── chunks_meta.json        # Human-readable chunk data (JSON sidecar)
    └── index_meta.json         # {"embedding_model": "fast"} — prevents model mismatch
```

### Q: How does the system reload indexes after restart?

On restart, memory caches are empty. When a request arrives for a previously-indexed URL:

```python
# mcp_server/services/cache.py
def get_retriever_with_disk_fallback(url_hash, embeddings=None):
    # 1. Check memory cache
    cached = retriever_cache.get(url_hash)
    if cached is not None:
        return cached, "memory"

    # 2. Check disk
    loaded = EnhancedRetriever.load_from_disk(url_hash, embeddings)
    if loaded is not None:
        retriever_cache.put(url_hash, loaded)  # promote to memory for next time
        return loaded, "disk"

    return None, None
```

**Auto-model selection on load:**
```python
# mcp_server/services/retrieval.py
@classmethod
def load_from_disk(cls, url_hash, embeddings=None):
    saved_model = cls.get_disk_embedding_model(url_hash)
    if embeddings is None:
        embeddings = (
            get_embeddings_accurate() if saved_model == "accurate"
            else get_embeddings_fast()
        )
    vectorstore = FAISS.load_local(index_dir, embeddings, allow_dangerous_deserialization=True)
```

This prevents the **vector-space mismatch bug** — an index built with MiniLM can't be queried with BGE (different vector spaces).

---

## 8. FAISS Disk Persistence

### Q: What is FAISS and why is it used in vector search?

FAISS (Facebook AI Similarity Search) is a library for **efficient similarity search on dense vectors**. It's used because:

1. **Speed** — Brute-force cosine similarity on 1000 vectors takes <1ms
2. **Memory efficiency** — Vectors are stored as contiguous float32 arrays in C++
3. **Scalability** — Supports approximate nearest neighbor (ANN) indexes for millions of vectors
4. **LangChain integration** — `langchain_community.vectorstores.FAISS` provides a seamless API
5. **No external service** — Unlike Pinecone or Weaviate, FAISS runs in-process (no network latency, no deployment complexity)

### Q: How does FAISS store vector indexes on disk?

```python
# mcp_server/services/retrieval.py
def save_to_disk(self, url_hash: str) -> None:
    index_dir = os.path.join(FAISS_INDEX_PATH, url_hash)
    os.makedirs(index_dir, exist_ok=True)

    # 1. Save FAISS binary index + LangChain docstore
    self.vectorstore.save_local(index_dir)

    # 2. Save human-readable chunk metadata
    meta = [{"page_content": c.page_content, "metadata": c.metadata} for c in self.chunks]
    with open(os.path.join(index_dir, "chunks_meta.json"), "w") as f:
        json.dump(meta, f)

    # 3. Save embedding model metadata (prevents mismatch)
    with open(os.path.join(index_dir, "index_meta.json"), "w") as f:
        json.dump({"embedding_model": self.embedding_model_name}, f)
```

### Q: What are the advantages of persistent vector indexes?

1. **Server restart resilience** — Indexes survive `Ctrl+C` / crash / redeployment
2. **Zero cold-start for repeat queries** — First request after restart loads from disk (~1s) instead of rebuilding (~10s)
3. **Cross-worker sharing** — In `--workers 4` mode, all workers can read the same disk indexes
4. **Debuggability** — `chunks_meta.json` lets you inspect what was indexed without loading FAISS
5. **Cost savings** — Embedding computation is eliminated for cached documents

---

## 9. File Re-Index Detection

### Q: How does the system detect if a document has already been indexed?

The detection uses a **URL-based hash**:

```python
url_hash = hashlib.sha256(document_url.encode()).hexdigest()[:16]
```

Then a three-tier check:
```python
# 1. Memory cache (fastest — ~0.001ms)
retriever = retriever_cache.get(url_hash)

# 2. Disk persistence (fast — ~100ms)
retriever = EnhancedRetriever.load_from_disk(url_hash)

# 3. Neither found → build fresh (~5-15 seconds)
```

### Q: Are file hashes used for deduplication?

We use **URL hashes**, not content hashes:

- `hashlib.sha256(document_url.encode())` — the URL string is hashed
- Same URL = same hash = same cached/indexed document
- Different URLs pointing to the same content = different hashes = indexed separately

**Why URL-based instead of content-based:**
- Content hashing requires downloading the file first (defeats the purpose of cache-first checks)
- URL-based hashing allows disk fallback checks without any network I/O
- For the rare case of identical content at different URLs, the cost of duplicate indexing is acceptable

### Q: How does the system update indexes when a document changes?

The system uses **TTL-based cache invalidation**:

- Memory caches expire after **30 minutes** (configurable via `cache_config.default_ttl`)
- After TTL expires, the next request re-downloads, re-processes, and re-indexes the document
- Disk indexes have **no TTL** — they persist indefinitely

To force a re-index:
1. Call `manage_cache(action="clear")` via MCP — clears all memory caches
2. Delete the `faiss_indexes/<url_hash>/` directory manually
3. Wait 30 minutes for TTL expiry (automatic)

---

## 10. Concurrency and Performance Optimizations

### Q: What is GPU semaphore concurrency?

A **GPU semaphore** is an `asyncio.Semaphore` that limits how many concurrent heavy compute operations (FAISS builds, embedding generation, retrieval) can run simultaneously.

Implementation in `mcp_server/core/concurrency.py`:

```python
_GPU_CONCURRENCY = int(os.getenv("GPU_CONCURRENCY", "2"))

gpu_semaphore = None  # lazily initialised

_gpu_pool = ThreadPoolExecutor(
    max_workers=_GPU_CONCURRENCY + 1,
    thread_name_prefix="gpu-pool",
)
```

### Q: Why is GPU access controlled using semaphores?

Without a semaphore, if 20 requests arrive simultaneously:
- All 20 start building FAISS indexes at once
- Each loads an embedding model batch into GPU memory
- GPU runs out of memory → **OOM crash**
- Or: CPU saturated → **extreme slowdown** (20× slower per request)

With the semaphore (default 2):
- Only 2 requests run FAISS/embedding operations concurrently
- The other 18 `await` on the semaphore (non-blocking — event loop still serves I/O)
- Total throughput is higher because resources aren't wasted on context switching

### Q: How does GPU semaphore implementation prevent overload?

```python
async def run_in_gpu_pool(fn, *args):
    """Run fn in the GPU thread-pool, guarded by the GPU semaphore."""
    sem = _ensure_semaphore()
    loop = asyncio.get_running_loop()
    async with sem:  # waits if 2 are already running
        return await loop.run_in_executor(_gpu_pool, fn, *args)
```

**Key details:**
1. `async with sem` — If both semaphore slots are taken, this coroutine suspends (yields to event loop)
2. `run_in_executor(_gpu_pool, fn, *args)` — Runs the CPU/GPU-bound function in a **dedicated thread pool** (separate from the default executor)
3. When one of the 2 running tasks finishes, a waiting task is unblocked
4. The dedicated `_gpu_pool` has `_GPU_CONCURRENCY + 1` threads (3 by default) — one extra to avoid deadlock

**Why a separate thread pool?**
- The default asyncio executor is shared with I/O-bound tasks (file reads, JSON parsing)
- If GPU tasks consumed all default threads, I/O tasks would starve
- A dedicated pool isolates GPU-bound work from I/O-bound work

### Q: What is async lock coalescing?

Async lock coalescing ensures that when **multiple concurrent requests need the same resource**, only **one** does the expensive work. The others wait and then use the result.

Implementation in `mcp_server/core/concurrency.py`:

```python
_build_locks: dict[str, asyncio.Lock] = {}  # one lock per url_hash

async def coalesced_build(url_hash: str, build_fn):
    """Only one coroutine builds per url_hash. Others wait."""
    lock = await _get_build_lock(url_hash)
    async with lock:
        return await build_fn()
```

### Q: How does async lock coalescing prevent duplicate work?

**Scenario without coalescing:**
```
10 requests for same URL arrive at t=0
→ All 10 check cache: MISS
→ All 10 start building FAISS index (10× redundant work!)
→ GPU overloaded, 10× the memory, 10× the time
```

**Scenario with coalescing:**
```
10 requests for same URL arrive at t=0
→ All 10 check cache: MISS
→ Request #1 acquires lock, starts building
→ Requests #2-10 block on async with lock
→ Request #1 finishes, stores in cache, releases lock
→ build_fn for Requests #2-10 starts:
     # Re-check cache (now HIT!)
     ret, src = get_retriever_with_disk_fallback(url_hash)
     if ret is not None:
         return ret, len(ret.chunks)  # use cached result
→ 1× build instead of 10×
```

The key is the **re-check inside `build_fn`** in `mcp_server/tools/query.py`:
```python
async def _build_index():
    # Re-check cache — another coroutine may have finished building
    ret, src = get_retriever_with_disk_fallback(url_hash)
    if ret is not None:
        return ret, len(ret.chunks)
    # ... build fresh only if still not cached ...
```

---

## 11. Advanced Retrieval Optimizations

### Q: What is adaptive chunking and how does it improve retrieval quality?

Our `AdaptiveChunkingStrategy` in `mcp_server/services/chunking.py` creates chunks that are:

1. **Type-aware** — Different chunk sizes for different document types
2. **Length-adaptive** — Chunk size scales with document length
3. **Importance-scored** — Each chunk gets a 0.0–1.0 quality score
4. **Content-typed** — Each chunk is classified as "text", "table", "list", or "heading"

**Why this improves retrieval:**
- Fixed-size chunks (the naive approach) split sentences mid-word and create meaningless fragments
- Our recursive splitter respects natural boundaries (`\n\n` → `\n` → `. ` → ` `)
- Small documents get small chunks (fine-grained retrieval), large documents get large chunks (preserves context)
- Importance scores let the diversity filter prefer high-quality chunks

### Q: How are chunk sizes determined?

```python
# mcp_server/services/chunking.py
params_map = {
    "pdf":  (1500, 300, ["\n\n", "\n", ". ", " "]),    # chunk=1500, overlap=300
    "pptx": (800,  150, ["\n---\n", "\n\n", "\n", ". ", " "]),
    "xlsx": (1200, 200, ["\n===", "\n---", "\n\n", "\n", " "]),
    "csv":  (1200, 200, ["\n===", "\n---", "\n\n", "\n", " "]),
    "docx": (1500, 300, ["\n\n", "\n", ". ", " "]),
    "html": (1500, 300, ["\n\n", "\n", ". ", " "]),
}

# Dynamic scaling based on document length:
if length > 100_000:        # very large document
    chunk_size *= 1.5       # bigger chunks to reduce total count
    overlap *= 1.3          # more overlap to preserve context
elif length < 5_000:        # very short document
    chunk_size //= 2        # smaller chunks for fine-grained retrieval
    overlap //= 2
```

**Why different sizes per type?**
- **PDF/DOCX** (1500 chars): Long-form text with paragraphs. Large chunks preserve complete thoughts.
- **PPTX** (800 chars): Slides are concise. Smaller chunks = one slide ≈ one chunk.
- **XLSX/CSV** (1200 chars): Tables need enough rows per chunk to be meaningful.

### Q: How does the cross-encoder reranker work?

After FAISS returns initial candidates, we rerank them with a **cross-encoder**:

```python
def _rerank(self, query, docs, top_k):
    reranker = get_reranker()
    pairs = [[query, d.page_content] for d in docs]
    scores = reranker.predict(pairs)
    ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
    return [d for d, _ in ranked[:top_k]]
```

**Why reranking improves results:**
- FAISS uses **bi-encoder** similarity — query and chunk are embedded independently, then compared. This is fast but approximate.
- The cross-encoder sees the **full query-chunk pair** together — it can capture fine-grained semantic relationships.
- FAISS returns 3× candidates (e.g., 15 for top_k=5), the cross-encoder picks the best 5.

**The three ML models:**

| Model | Type | Speed | Accuracy | Use |
|-------|------|-------|----------|-----|
| `all-MiniLM-L6-v2` | Bi-encoder | Fast | Good | Embedding ≤50 chunks |
| `bge-small-en-v1.5` | Bi-encoder | Medium | Better | Embedding >50 chunks |
| `ms-marco-MiniLM-L-6-v2` | Cross-encoder | Slow | Best | Reranking top candidates |

---

## 12. Connection Management

### Q: What is connection pooling?

Connection pooling maintains a **set of pre-opened HTTP connections** that can be reused across multiple requests, avoiding the overhead of creating a new connection for each download.

Our implementation in `mcp_server/services/downloader.py`:

```python
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
            headers={"User-Agent": "MCP-RAG-Server/2.0"},
            limits=httpx.Limits(
                max_connections=20,           # max 20 simultaneous connections
                max_keepalive_connections=10,  # keep 10 idle connections alive
            ),
        )
    return _client
```

**Why this matters:**
- Without pooling: Each download creates a new TCP connection (DNS lookup + TCP handshake + TLS handshake = ~100-500ms)
- With pooling: Reuse existing connections (0ms overhead for the handshake)
- `max_connections=20` prevents exhausting OS file descriptors under heavy load
- `max_keepalive_connections=10` keeps 10 idle connections warm for rapid reuse

### Q: How does exponential backoff work?

Exponential backoff increases the **wait time between retries** to avoid overwhelming a failing server:

```python
# mcp_server/services/downloader.py
MAX_RETRIES = 3
RETRY_BACKOFF = [1, 3, 5]  # seconds

async def download(url: str) -> bytes:
    for attempt in range(MAX_RETRIES):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                raise  # 4xx = client error, don't retry
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]  # 1s, 3s, 5s
                await asyncio.sleep(wait)
        except (httpx.RequestError, httpx.TimeoutException):
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF[attempt]
                await asyncio.sleep(wait)
    raise DownloadError(f"Failed after {MAX_RETRIES} attempts")
```

**Retry schedule:**
```
Attempt 1: immediate
  → fails (5xx or timeout)
  → wait 1 second
Attempt 2: t = 1s
  → fails
  → wait 3 seconds
Attempt 3: t = 4s
  → fails
  → raise DownloadError
```

**Key design decisions:**
- Only **5xx errors** and **network errors** trigger retries (not 4xx — those are permanent)
- Uses `asyncio.sleep` instead of `time.sleep` — doesn't block the event loop
- Total retry time: 1 + 3 + 5 = **9 seconds max** before giving up

### Q: Why is exponential backoff used in distributed systems?

1. **Thundering herd prevention** — If a server goes down and 1000 clients immediately retry, the server is overwhelmed the moment it comes back. Backoff spreads retries over time.
2. **Transient error recovery** — Many failures (network glitches, temporary overload) resolve within seconds.
3. **Rate limit compliance** — If a server returns 429, backing off gives it time to recover.

---

## 13. System Architecture Questions

### Q: What is the overall architecture of the project?

The system follows a **6-layer production architecture**:

```
Layer 1: TRANSPORT           ← streamable-http | stdio
Layer 2: MIDDLEWARE           ← rate-limit → validation → logging → timeout
Layer 3: TOOLS (13 MCP)      ← process, chunk, retrieve, extract×6, query, detect, health, cache
Layer 4: SERVICES             ← downloader, cache (3-layer), chunking, retrieval, language
Layer 5: PROCESSORS           ← PDF, DOCX, PPTX, XLSX/CSV, Image/OCR, HTML/TXT, URL
Layer 6: ML MODELS            ← MiniLM-L6-v2, BGE-small-en, ms-marco reranker
```

**Key architectural principles:**
- **No LLM inside** — The server is a pure deterministic tool. Intelligence lives in the external AI agent.
- **Transport-agnostic** — Layers 2–6 don't know which transport is active.
- **Fail-safe** — Optional features (OCR, reranking, language detection) degrade gracefully via feature flags.
- **Cache-first** — Every expensive operation is cached at the appropriate layer.

### Q: How does FastMCP and the vector database interact?

```
FastMCP (server.py + tools/)
    │
    ├─ @mcp.tool() retrieve_chunks
    │   → @guarded decorator → rate limit → timeout
    │   → download() → process() → chunk() → EnhancedRetriever → FAISS
    │
    ├─ @mcp.tool() process_document
    │   → download() → process() → return extracted content
    │
    └─ @mcp.tool() extract_pdf_text, extract_docx_text, ...
        → download() → format-specific processor → return text

All tools call the SAME shared service layer:
    services/downloader.py     → download bytes
    services/cache.py          → 3-layer cache
    services/chunking.py       → AdaptiveChunkingStrategy
    services/retrieval.py      → EnhancedRetriever (FAISS wrapper)
    core/models.py             → embedding models
```

### Q: What happens from the moment a user asks a question until they get an answer?

**Complete end-to-end flow:**

```
1. USER types a question in the client:
   > "What is the revenue in this report? https://example.com/report.pdf"

2. CLIENT (agent.py) creates a LangChain agent with MCP tools:
   → MultiServerMCPClient connects to http://127.0.0.1:8000/mcp
   → Discovers all 13 tools via MCP tools/list

3. LLM (Gemini/GPT) receives the query + tool definitions
   → Decides to call retrieve_chunks(document_url="...", query="revenue")

4. CLIENT sends MCP tool call via streamable-http to server:
   POST /mcp  {"method":"tools/call","params":{"name":"retrieve_chunks",...}}

5. SERVER processes retrieve_chunks:
   a. @guarded → request_id → check_rate_limit() → log start
   b. validate_url(document_url) — regex check
   c. url_hash = SHA-256(url)[:16]
   d. Check document_cache → MISS
   e. download(url) → check download_cache → MISS → httpx.get(url) → cache bytes
   f. detect_document_type(url) → "pdf"
   g. TargetedDocumentProcessor.process_document():
      → EnhancedPDFProcessor.extract_pdf_content(bytes)
      → PyMuPDF: open stream → iterate pages → extract text blocks
      → URLExtractor: scan text for URLs
      → detect_language_robust: 3-round majority vote → "en"
      → Returns ProcessedDocument
   h. put_cached_document(url_hash, processed)
   i. Check retriever_cache → memory MISS → disk MISS
   j. coalesced_build(url_hash, _build_index):
      → AdaptiveChunkingStrategy.create_chunks(content, "pdf")
      → Select embedding model: ≤50 chunks → "fast", >50 → "accurate"
      → await run_in_gpu_pool(EnhancedRetriever, embeddings, chunks, model_name)
        → GPU semaphore acquired (max 2 concurrent)
        → FAISS.from_documents(chunks, embeddings) — vectorize all chunks
      → put_retriever_with_disk(url_hash, retriever)
        → Memory cache + disk persist (index.faiss + chunks_meta.json + index_meta.json)
   k. await run_in_gpu_pool(retriever.retrieve, "revenue", 5)
      → FAISS similarity_search(query, k=15) — fetch 3× candidates
      → Cross-encoder rerank: rank 15 candidates by relevance
      → Diversity filter: prefer varied content types
      → Return top 5 chunks

6. SERVER returns MCP response:
   {"content":[{"type":"text","text":"{\"results\":[...],\"total_chunks_indexed\":42}"}]}

7. CLIENT receives tool result → LLM reads the chunks
   → LLM synthesizes answer: "According to the report, revenue increased by 15% to $42 million."

8. USER sees the answer.
```

### Q: What components are involved in the RAG pipeline?

| # | Component | Module | Role |
|---|-----------|--------|------|
| 1 | Downloader | `services/downloader.py` | HTTP download with 3× retry, connection pooling, cache |
| 2 | Type Detector | `processors/__init__.py` | URL extension → document type mapping |
| 3 | Processors | `processors/*.py` | Format-specific text/table extraction |
| 4 | Language Detector | `services/language.py` | 3-round majority-vote language detection |
| 5 | Chunker | `services/chunking.py` | Adaptive chunking with importance scoring |
| 6 | Embedder | `core/models.py` | MiniLM-L6-v2 / BGE-small-en sentence embeddings |
| 7 | Indexer | `services/retrieval.py` | FAISS vector index construction |
| 8 | Retriever | `services/retrieval.py` | Similarity search on FAISS index |
| 9 | Reranker | `core/models.py` | Cross-encoder ms-marco reranking |
| 10 | Diversity Filter | `services/retrieval.py` | Importance + content-type diversity |
| 11 | Cache | `services/cache.py` | 3-layer TTL cache (download, document, retriever) |
| 12 | Disk Persistence | `services/retrieval.py` | FAISS index save/load from `faiss_indexes/` |

---

## 14. Production-Level Questions

### Q: How would you scale this system for millions of users?

**Horizontal scaling strategy:**

```
                    Load Balancer (nginx / AWS ALB)
                         │
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
      ┌─────────┐  ┌─────────┐  ┌─────────┐
      │ Worker 1│  │ Worker 2│  │ Worker 3│  ← Uvicorn workers (--workers N)
      │ GPU: 0  │  │ GPU: 0  │  │ GPU: 1  │
      └────┬────┘  └────┬────┘  └────┬────┘
           │             │             │
           └─────────────┼─────────────┘
                         ▼
              ┌──────────────────┐
              │  Shared Storage  │
              │  (NFS / S3)      │
              │  faiss_indexes/  │
              └──────────────────┘
```

**Scaling dimensions:**

1. **Multi-worker deployment** (already supported):
   ```bash
   python -m mcp_server --transport streamable-http --workers 4
   ```
   Runs 4 Uvicorn workers. Each is a separate process with its own memory, sharing the same FAISS disk indexes.

2. **Kubernetes deployment:**
   - Deploy as a `Deployment` with N pods
   - Use a `PersistentVolumeClaim` for `faiss_indexes/` (shared NFS)
   - `GET /health` endpoint serves as liveness/readiness probe (provided by `MCPRouter`)
   - `HorizontalPodAutoscaler` scales based on CPU/GPU utilisation

3. **Separate compute from storage:**
   - Move FAISS indexes to a **central vector database** (Pinecone, Weaviate, Qdrant)
   - Each worker becomes stateless — truly horizontal

4. **GPU acceleration:**
   - Replace `faiss-cpu` with `faiss-gpu` for 10–100× faster vector search
   - Our `DEVICE` detection already supports CUDA:
     ```python
     if torch.cuda.is_available():
         DEVICE = "cuda"
     ```

5. **Rate limiting at scale:**
   - Replace in-memory `_TokenBucket` with **Redis-backed** rate limiting
   - Currently, each worker has its own rate limit state — not shared
   - Redis provides a single source of truth across workers

### Q: How would you monitor and log the system?

**Our existing structured logging** (`mcp_server/core/logging.py`):

Every log is a JSON object written to stderr + file:

```json
{
  "ts": "2026-03-08T10:30:45.123456+00:00",
  "level": "INFO",
  "logger": "mcp_server.rag_pipeline",
  "msg": "rag.step.5.embedding.done — FAISS index built & persisted to disk",
  "rid": "a1b2c3d4e5f6",
  "tool": "retrieve_chunks",
  "detail": "indexed 42 chunks",
  "elapsed": 3.45
}
```

**Production monitoring stack:**

1. **Log aggregation:**
   - Ship JSON logs to **ELK Stack** or **Grafana Loki**
   - Filter by `rid` (request ID) to trace a full request lifecycle
   - Alert on `level: "ERROR"` or `code: "RATE_LIMITED"`

2. **Health endpoint** (already implemented via `MCPRouter`):
   ```
   GET /health → {"status": "healthy", "models_loaded": true, ...}
   ```

3. **Cache monitoring:**
   - The `manage_cache(action="stats")` MCP tool returns hit/miss rates per layer
   - Alert if hit rate drops below 50% (might indicate cache too small)

4. **Request tracing:**
   - Every request gets a unique `request_id` via `ContextVar`
   - The ID is included in every log entry
   - Enables end-to-end tracing through the middleware → tool → service → model pipeline

### Q: How would you handle failures in embedding or vector search services?

Our system handles failures with multiple defense layers:

**1. Model load failure:**
```python
# mcp_server/core/models.py
try:
    _ensure_models_loaded()
except Exception as exc:
    raise ModelLoadError(f"Failed to initialise models: {exc}")
```
- The `ModelLoadError` propagates to the tool call → returned as MCP error to client
- The `get_system_health()` tool reports `"models_loaded": false`

**2. FAISS build failure:**
```python
# mcp_server/services/retrieval.py
def save_to_disk(self, url_hash):
    try:
        self.vectorstore.save_local(index_dir)
    except Exception as exc:
        logger.warning("faiss.save_failed", extra={"error": str(exc)})
        # Continues — index is still in memory, just not persisted
```
- Disk save failure is **non-fatal** — the index stays in memory cache
- Next server restart will need to rebuild (graceful degradation)

**3. Reranking failure:**
```python
# mcp_server/services/retrieval.py
def _rerank(self, query, docs, top_k):
    try:
        reranker = get_reranker()
        scores = reranker.predict(pairs)
        ranked = sorted(zip(docs, scores), key=lambda x: x[1], reverse=True)
        return [d for d, _ in ranked[:top_k]]
    except Exception:
        logger.warning("reranking.failed")
        return docs[:top_k]  # fallback: return unranked results
```
- Reranking failure → graceful fallback to FAISS similarity scores

**4. Download failure:**
- 3× retries with exponential backoff (1s, 3s, 5s)
- 4xx errors fail fast (bad URL, not found, forbidden)
- After 3 failures → `DownloadError` → returned as MCP error dict

**5. Timeout enforcement:**
```python
# mcp_server/middleware/__init__.py
result = await asyncio.wait_for(fn(*args, **kwargs), timeout=_timeout)
```
- Every tool has a maximum execution time (default 300 seconds)
- Hung downloads or stuck FAISS builds are killed cleanly

**6. Feature flag degradation:**
```python
# mcp_server/core/config.py
RERANK_AVAILABLE = True
try:
    from sentence_transformers import CrossEncoder
except ImportError:
    RERANK_AVAILABLE = False  # gracefully degrade
```
- If `sentence_transformers` isn't installed → reranking disabled, not crashed
- If `pytesseract` isn't installed → OCR disabled, not crashed
- If `langdetect` isn't installed → language detection defaults to "en"

**Summary of failure handling:**

| Failure | Response | Impact |
|---------|----------|--------|
| Model load fails | `ModelLoadError` → MCP error | No tools work until models load |
| FAISS build fails | Exception → MCP error dict | Single query fails, others unaffected |
| FAISS save fails | Warning logged, continues | Index in memory only (lost on restart) |
| Reranking fails | Fallback to unranked results | Slightly lower accuracy |
| Download fails (5xx) | 3× retry → `DownloadError` | Single query fails |
| Download fails (4xx) | Immediate `DownloadError` | Permanent — bad URL |
| Timeout exceeded | `asyncio.TimeoutError` → error | Tool call fails cleanly |
| OCR unavailable | Feature flag → skip OCR | Images return empty text |

---

## Appendix: Key Files Reference

| File | Purpose |
|------|---------|
| `mcp_server/__main__.py` | CLI entry point — transport selection (streamable-http or stdio) |
| `mcp_server/server.py` | FastMCP instance + lifespan + tool registration via side-effect imports |
| `mcp_server/_asgi.py` | ASGI app factory — `MCPRouter(mcp.streamable_http_app())` |
| `mcp_server/core/config.py` | Centralised config (frozen dataclasses: server, model, cache, security) |
| `mcp_server/core/models.py` | ML model loading (embeddings + reranker) with double-checked locking |
| `mcp_server/core/errors.py` | Exception hierarchy (MCPServerError → RateLimit, Validation, ...) |
| `mcp_server/core/logging.py` | Structured JSON logging to stderr + rotating file |
| `mcp_server/core/concurrency.py` | GPU semaphore + async lock coalescing |
| `mcp_server/core/schemas.py` | Domain dataclasses (ProcessedDocument, ExtractedTable, etc.) |
| `mcp_server/middleware/__init__.py` | `@guarded` decorator (rate-limit, timeout, logging) |
| `mcp_server/middleware/guards.py` | TokenBucket rate limiter, MCPRouter, validators |
| `mcp_server/services/cache.py` | 3-layer TTL cache with disk fallback |
| `mcp_server/services/downloader.py` | HTTP download with retry + connection pooling |
| `mcp_server/services/chunking.py` | Adaptive chunking strategy with importance scoring |
| `mcp_server/services/retrieval.py` | FAISS retriever with reranking + disk persistence |
| `mcp_server/services/language.py` | 3-round majority-vote language detection |
| `mcp_server/processors/*.py` | Format-specific extractors (PDF, DOCX, PPTX, XLSX, Image, URL) |
| `mcp_server/tools/query.py` | Core tools: process, chunk, retrieve, query_spreadsheet |
| `mcp_server/tools/extract.py` | Extraction tools: pdf, docx, pptx, xlsx, csv, image |
| `mcp_server/tools/utility.py` | Utility tools: language, health, cache |
| `mcp_server/resources/__init__.py` | MCP resources: supported-formats, tool-descriptions |
| `client/agent.py` | LangChain MCP agent (Gemini/GPT + MultiServerMCPClient) |
