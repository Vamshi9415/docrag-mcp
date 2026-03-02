param(
    [string]$ServerHost = "127.0.0.1",
    [int]   $Port       = 8000,
    [string]$ApiKey     = "vamshibachumcpserver"
)

$BaseUrl = "http://${ServerHost}:${Port}"
$McpUrl  = "${BaseUrl}/mcp"
$MCP_BODY = '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test-client","version":"1.0"}}}'

$PASS = 0
$FAIL = 0

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host ("-" * 60) -ForegroundColor DarkGray
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host ("-" * 60) -ForegroundColor DarkGray
}

function Assert-Status {
    param(
        [string]$TestName,
        [int]   $Got,
        [int]   $Expected,
        [string]$Body = ""
    )
    if ($Got -eq $Expected) {
        Write-Host "  [PASS] $TestName" -ForegroundColor Green
        Write-Host "         Status: $Got (expected $Expected)" -ForegroundColor DarkGreen
        $script:PASS++
    } else {
        Write-Host "  [FAIL] $TestName" -ForegroundColor Red
        Write-Host "         Status: $Got (expected $Expected)" -ForegroundColor DarkRed
        $script:FAIL++
    }
    if ($Body) {
        Write-Host "         Body  : $($Body.Substring(0, [Math]::Min(120, $Body.Length)))" -ForegroundColor DarkGray
    }
}

function Invoke-McpRequest {
    param(
        [hashtable]$Headers,
        [string]   $Body   = $MCP_BODY,
        [string]   $Method = "POST"
    )
    $result = @{ status = 0; body = "" }
    try {
        $resp = Invoke-WebRequest -Uri $McpUrl -Method $Method -Headers $Headers -Body $Body -UseBasicParsing -ErrorAction Stop
        $result.status = [int]$resp.StatusCode
        $result.body   = $resp.Content
    } catch {
        if ($_.Exception.Response) {
            $result.status = [int]$_.Exception.Response.StatusCode.value__
            $stream = $_.Exception.Response.GetResponseStream()
            if ($stream) { $result.body = [System.IO.StreamReader]::new($stream).ReadToEnd() }
        } else {
            $result.status = -1
            $result.body   = $_.Exception.Message
        }
    }
    return $result
}

Write-Host ""
Write-Host "  MCP Server Auth Tests" -ForegroundColor White
Write-Host "  Target : $McpUrl" -ForegroundColor DarkGray
Write-Host "  API Key: $ApiKey" -ForegroundColor DarkGray
Write-Host ""

try {
    Invoke-WebRequest -Uri "${BaseUrl}/mcp" -Method GET -UseBasicParsing -ErrorAction Stop | Out-Null
} catch {
    if (-not $_.Exception.Response) {
        Write-Host "  [ERROR] Server not reachable at $BaseUrl" -ForegroundColor Red
        Write-Host "          Start the server first:" -ForegroundColor Yellow
        Write-Host "            python -m mcp_server --transport streamable-http --port $Port" -ForegroundColor Yellow
        exit 1
    }
}
Write-Host "  Server is reachable at $BaseUrl" -ForegroundColor DarkGreen

# TEST 1 - No API key
Write-Section "TEST 1 - No API key (expect 401)"
$h = @{ "Content-Type" = "application/json"; "Accept" = "application/json, text/event-stream" }
$r = Invoke-McpRequest -Headers $h
Assert-Status -TestName "Request without x-api-key header" -Got $r.status -Expected 401 -Body $r.body

# TEST 2 - Empty API key
Write-Section "TEST 2 - Empty API key value (expect 401)"
$h = @{ "x-api-key" = ""; "Content-Type" = "application/json"; "Accept" = "application/json, text/event-stream" }
$r = Invoke-McpRequest -Headers $h
Assert-Status -TestName "Request with empty x-api-key value" -Got $r.status -Expected 401 -Body $r.body

# TEST 3 - Wrong API key
Write-Section "TEST 3 - Wrong API key (expect 401)"
$h = @{ "x-api-key" = "wrongkey-totally-invalid-12345"; "Content-Type" = "application/json"; "Accept" = "application/json, text/event-stream" }
$r = Invoke-McpRequest -Headers $h
Assert-Status -TestName "Request with incorrect x-api-key value" -Got $r.status -Expected 401 -Body $r.body

# TEST 4 - Correct API key
Write-Section "TEST 4 - Correct API key (expect 200 + MCP handshake)"
$h = @{ "x-api-key" = $ApiKey; "Content-Type" = "application/json"; "Accept" = "application/json, text/event-stream" }
$r = Invoke-McpRequest -Headers $h
Assert-Status -TestName "Full MCP initialize with valid x-api-key" -Got $r.status -Expected 200 -Body $r.body
if ($r.status -eq 200) {
    if ($r.body -match '"protocolVersion"') { Write-Host "         MCP handshake: protocolVersion confirmed" -ForegroundColor DarkGreen }
    if ($r.body -match '"serverInfo"')      { Write-Host "         MCP handshake: serverInfo confirmed" -ForegroundColor DarkGreen }
}

# TEST 5 - Correct key, wrong HTTP method (GET instead of POST)
# Auth passes but FastMCP rejects the method - proves auth middleware runs first
Write-Section "TEST 5 - Correct key, GET method (expect 4xx, not 401)"
$h = @{ "x-api-key" = $ApiKey; "Accept" = "application/json, text/event-stream" }
$r = Invoke-McpRequest -Headers $h -Method "GET"
$got = $r.status
$pass5 = ($got -ne 401)
if ($pass5) {
    Write-Host "  [PASS] Auth passed, FastMCP rejected wrong method" -ForegroundColor Green
    Write-Host "         Status: $got (not 401 - auth passed; FastMCP rejected the method)" -ForegroundColor DarkGreen
    $PASS++
} else {
    Write-Host "  [FAIL] GET /mcp with valid key" -ForegroundColor Red
    Write-Host "         Status: $got (expected any status other than 401)" -ForegroundColor DarkRed
    $FAIL++
}

# Summary
Write-Host ""
Write-Host ("=" * 60) -ForegroundColor DarkGray
$total = $PASS + $FAIL
if ($FAIL -eq 0) {
    Write-Host "  RESULT: All $total tests passed" -ForegroundColor Green
} else {
    Write-Host "  RESULT: $PASS/$total passed, $FAIL failed" -ForegroundColor Red
}
Write-Host ("=" * 60) -ForegroundColor DarkGray
Write-Host ""

exit $FAIL

