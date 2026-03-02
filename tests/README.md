# MCP Server — API Tests

This folder contains test scripts that verify:
1. API key authentication (`x-api-key`) is correctly enforced on the MCP `streamable-http` transport
2. The standard GET endpoints (`/health`, `/info`) behave correctly

---

## Files

| File | Description |
|------|-------------|
| `test_mcp_auth.ps1` | PowerShell script — 8 tests covering auth on `/mcp` and the GET `/health`/`/info` endpoints |

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

### POST /mcp — MCP protocol endpoint

| # | Test | Sent Header | Expected Status | What it proves |
|---|------|-------------|-----------------|----------------|
| 1 | No API key | *(none)* | **401** | Requests without any key are blocked |
| 2 | Empty key value | `x-api-key: ` | **401** | Empty string is treated as missing |
| 3 | Wrong API key | `x-api-key: wrongkey-...` | **401** | Invalid keys are rejected |
| 4 | Correct API key | `x-api-key: vamshibachumcpserver` | **200** | Valid key passes; full MCP handshake completes |
| 5 | Correct key, GET method | `x-api-key: vamshibachumcpserver` | **not 401** | Auth passes; FastMCP rejects wrong method — proves middleware order |

### Standard GET endpoints

| # | Endpoint | Sent Header | Expected Status | What it proves |
|---|----------|-------------|-----------------|----------------|
| 6 | `GET /health` | *(none)* | **200** | Liveness probe is exempt from auth — always reachable by infra |
| 7 | `GET /info` | *(none)* | **401** | Capabilities endpoint requires auth |
| 8 | `GET /info` | `x-api-key: vamshibachumcpserver` | **200** | Valid key returns server version, features, supported formats |

---

## Expected Output

A fully passing run looks like:

```
  MCP Server Auth + GET Endpoint Tests
  Target : http://127.0.0.1:8000
  API Key: vamshibachumcpserver

  Server is reachable at http://127.0.0.1:8000

------------------------------------------------------------
  TEST 1 - POST /mcp: no API key (expect 401)
------------------------------------------------------------
  [PASS] Request without x-api-key header
         Status: 401 (expected 401)

------------------------------------------------------------
  TEST 2 - POST /mcp: empty API key (expect 401)
------------------------------------------------------------
  [PASS] Request with empty x-api-key value
         Status: 401 (expected 401)

------------------------------------------------------------
  TEST 3 - POST /mcp: wrong API key (expect 401)
------------------------------------------------------------
  [PASS] Request with incorrect x-api-key
         Status: 401 (expected 401)

------------------------------------------------------------
  TEST 4 - POST /mcp: correct API key (expect 200 + MCP handshake)
------------------------------------------------------------
  [PASS] Full MCP initialize with valid x-api-key
         Status: 200 (expected 200)
         MCP handshake: protocolVersion confirmed
         MCP handshake: serverInfo confirmed

------------------------------------------------------------
  TEST 5 - POST /mcp: correct key, GET method (expect not 401)
------------------------------------------------------------
  [PASS] Auth passed; FastMCP rejected wrong method
         Status: 400 (not 401 - auth did not block this)

------------------------------------------------------------
  TEST 6 - GET /health: no key (expect 200 - exempt from auth)
------------------------------------------------------------
  [PASS] GET /health without x-api-key (liveness probe)
         Status: 200 (expected 200)
         Response: status field present
         Response: auth_enabled field present

------------------------------------------------------------
  TEST 7 - GET /info: no key (expect 401)
------------------------------------------------------------
  [PASS] GET /info without x-api-key
         Status: 401 (expected 401)

------------------------------------------------------------
  TEST 8 - GET /info: correct key (expect 200)
------------------------------------------------------------
  [PASS] GET /info with valid x-api-key (server capabilities)
         Status: 200 (expected 200)
         Capabilities: version present
         Capabilities: features present
         Capabilities: device present

============================================================
  RESULT: All 8 tests passed
============================================================
```

---

## How Authentication Works

```
Client Request
      │
      ▼
 AuthMiddleware (guards.py)
      │  GET /health? ──────────────────────────────► HTTP 200  (always, no key needed)
      │
      │  all other paths: check x-api-key header
      │
      ├─── key missing or wrong ──► HTTP 401  (request dies here)
      │
      └─── key correct ────────────────────────────────────────────────────────┐
                                                                               ▼
                                                                     MCPRouter (guards.py)
                                                                           │
                                                                     ┌─────┴──────┐
                                                               GET /health    GET /info
                                                               (200, probe)  (200, caps)
                                                                           │
                                                               everything else
                                                                           │
                                                                           ▼
                                                              FastMCP streamable_http_app()
                                                                    POST /mcp only
                                                                           │
                                                                           ▼
                                                                    Tool Execution
```

`AuthMiddleware` and `MCPRouter` are pure ASGI classes in `guards.py`. Auth
runs before anything else, so no MCP session is ever created for an
unauthenticated call. `GET /health` is whitelisted in `_AUTH_EXEMPT_PATHS` so
load-balancers and Kubernetes probes can always reach it.

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `[ERROR] Server not reachable` | Server not started | Run `python -m mcp_server --transport streamable-http` |
| Test 4 fails with 401 | Wrong key in `.env` | Check `MCP_API_KEY` in root `.env` matches `-ApiKey` param |
| Test 6 (`/health`) fails with 401 | `_AUTH_EXEMPT_PATHS` not applied | Restart server after code changes |
| Test 8 (`/info`) fails with 401 | Wrong key passed | Confirm `-ApiKey vamshibachumcpserver` matches root `.env` |
| `Auth: disabled` in server startup | `MCP_API_KEY` not set | Add `MCP_API_KEY=vamshibachumcpserver` to root `.env` |
| All tests return connection error | Port conflict | Change port: `python -m mcp_server --port 9000` and pass `-Port 9000` to the script |
