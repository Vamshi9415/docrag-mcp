# MCP Server — API Tests

This folder contains test scripts that verify the server's API key authentication
(`x-api-key`) is correctly enforced on the MCP `streamable-http` transport.

---

## Files

| File | Description |
|------|-------------|
| `test_mcp_auth.ps1` | PowerShell script — 5 auth tests against the `/mcp` endpoint |

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.10+ |
| PowerShell | 5.1+ (built into Windows) |
| MCP server dependencies | `pip install -r requirements.txt` |
| `.env` file configured | `MCP_API_KEY=vamshibachumcpserver` |

Ensure your `.env` file in the project root contains:

```env
MCP_API_KEY=vamshibachumcpserver
```

---

## Step 1 — Start the Server

Open a terminal in the project root and run:

```powershell
python -m mcp_server --transport streamable-http --port 8000
```

Wait until you see all three startup lines:

```
  RAG Document Server — MCP (streamable-http)
  Listening on  http://127.0.0.1:8000
  MCP endpoint: http://127.0.0.1:8000/mcp
  Auth: enabled (x-api-key required)
...
INFO:     Application startup complete.
```

> The server takes ~20 seconds on first startup due to ML model loading.

---

## Step 2 — Run the Tests

Open a **second** terminal in the project root and run:

```powershell
.\tests\test_mcp_auth.ps1
```

### Optional Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Host` | `127.0.0.1` | Server hostname |
| `-Port` | `8000` | Server port |
| `-ApiKey` | `vamshibachumcpserver` | API key to use for the "correct key" tests |

**Custom example:**

```powershell
.\tests\test_mcp_auth.ps1 -Host 127.0.0.1 -Port 9000 -ApiKey vamshibachumcpserver
```

---

## What the Tests Verify

| # | Test | Sent Header | Expected Status | What it proves |
|---|------|-------------|-----------------|----------------|
| 1 | No API key | *(none)* | **401** | Requests without any key are blocked |
| 2 | Empty key value | `x-api-key: ` | **401** | Empty string is treated as missing |
| 3 | Wrong API key | `x-api-key: wrongkey-...` | **401** | Invalid keys are rejected |
| 4 | Correct API key | `x-api-key: vamshibachumcpserver` | **200** | Valid key passes; full MCP handshake completes |
| 5 | Correct key, GET method | `x-api-key: vamshibachumcpserver` | **not 401** | Auth passes; FastMCP closes the connection for unsupported methods — proves middleware order |

---

## Expected Output

A fully passing run looks like:

```
  MCP Server Auth Tests
  Target : http://127.0.0.1:8000/mcp
  API Key: vamshibachumcpserver

  Server is reachable at http://127.0.0.1:8000

------------------------------------------------------------
  TEST 1 - No API key (expect 401)
------------------------------------------------------------
  [PASS] Request without x-api-key header
         Status: 401 (expected 401)
         Body  : {"error": "Invalid or missing API key", "code": "AUTH_ERROR"}

------------------------------------------------------------
  TEST 2 - Empty API key value (expect 401)
------------------------------------------------------------
  [PASS] Request with empty x-api-key value
         Status: 401 (expected 401)
         Body  : {"error": "Invalid or missing API key", "code": "AUTH_ERROR"}

------------------------------------------------------------
  TEST 3 - Wrong API key (expect 401)
------------------------------------------------------------
  [PASS] Request with incorrect x-api-key value
         Status: 401 (expected 401)
         Body  : {"error": "Invalid or missing API key", "code": "AUTH_ERROR"}

------------------------------------------------------------
  TEST 4 - Correct API key (expect 200 + MCP handshake)
------------------------------------------------------------
  [PASS] Full MCP initialize with valid x-api-key
         Status: 200 (expected 200)
         Body  : event: message ...
         MCP handshake: protocolVersion confirmed
         MCP handshake: serverInfo confirmed

------------------------------------------------------------
  TEST 5 - Correct key, GET method (expect 4xx, not 401)
------------------------------------------------------------
  [PASS] Auth passed, FastMCP rejected wrong method
         Status: -1 (not 401 - auth passed; FastMCP rejected the method)

============================================================
  RESULT: All 5 tests passed
============================================================
```

---

## How Authentication Works

```
Client Request
      │
      ▼
 AuthMiddleware (ASGI, guards.py)
      │  checks scope["headers"] for b"x-api-key"
      │  compares against MCP_API_KEY env var
      │
      ├─── key missing or wrong ──► HTTP 401  (request dies here)
      │
      └─── key correct ───────────► FastMCP streamable_http_app()
                                          │
                                          ▼
                                    MCP Protocol Layer (/mcp)
                                          │
                                          ▼
                                    Tool Execution
```

The `AuthMiddleware` is a pure ASGI class — it runs **before** FastMCP sees the
request, so no MCP session is ever created for an unauthenticated call.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `[ERROR] Server not reachable` | Server not started | Run `python -m mcp_server --transport streamable-http` |
| Test 4 fails with 401 | Wrong key in `.env` | Check `MCP_API_KEY` in root `.env` matches `-ApiKey` param |
| Test 4 fails with 406 | `Accept` header mismatch | Script issue — ensure the script hasn't been modified |
| `Auth: disabled` in server startup | `MCP_API_KEY` not set | Add `MCP_API_KEY=vamshibachumcpserver` to root `.env` |
| All tests return connection error | Port conflict | Change port: `python -m mcp_server --port 9000` and pass `-Port 9000` to the script |
