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
    param([string]$TestName,[int]$Got,[int]$Expected,[string]$Body = "")
    if ($Got -eq $Expected) {
        Write-Host "  [PASS] $TestName" -ForegroundColor Green
        Write-Host "         Status: $Got (expected $Expected)" -ForegroundColor DarkGreen
        $script:PASS++
    } else {
        Write-Host "  [FAIL] $TestName" -ForegroundColor Red
        Write-Host "         Status: $Got (expected $Expected)" -ForegroundColor DarkRed
        $script:FAIL++
    }
    if ($Body) { Write-Host "         Body  : $($Body.Substring(0, [Math]::Min(120, $Body.Length)))" -ForegroundColor DarkGray }
}

function Invoke-Http {
    param([string]$Url,[string]$Method="GET",[hashtable]$Headers=@{},[string]$Body="")
    $result = @{ status = 0; body = "" }
    try {
        $params = @{ Uri=$Url; Method=$Method; Headers=$Headers; UseBasicParsing=$true; ErrorAction="Stop" }
        if ($Body -ne "") { $params["Body"] = $Body }
        $resp = Invoke-WebRequest @params
        $result.status = [int]$resp.StatusCode
        $result.body   = $resp.Content
    } catch {
        if ($_.Exception.Response) {
            $result.status = [int]$_.Exception.Response.StatusCode.value__
            $stream = $_.Exception.Response.GetResponseStream()
            if ($stream) { $result.body = [System.IO.StreamReader]::new($stream).ReadToEnd() }
        } else { $result.status = -1; $result.body = $_.Exception.Message }
    }
    return $result
}

Write-Host ""
Write-Host "  MCP Server Auth + GET Endpoint Tests" -ForegroundColor White
Write-Host "  Target : $BaseUrl" -ForegroundColor DarkGray
Write-Host "  API Key: $ApiKey" -ForegroundColor DarkGray
Write-Host ""
$ping = Invoke-Http -Url "${BaseUrl}/health"
if ($ping.status -le 0) {
    Write-Host "  [ERROR] Server not reachable at $BaseUrl" -ForegroundColor Red
    Write-Host "          Start it: python -m mcp_server --transport streamable-http --port $Port" -ForegroundColor Yellow
    exit 1
}
Write-Host "  Server is reachable at $BaseUrl" -ForegroundColor DarkGreen

# ── MCP /mcp endpoint auth tests ────────────────────────────────────────────

Write-Section "TEST 1 - POST /mcp: no API key (expect 401)"
$r = Invoke-Http -Url $McpUrl -Method POST -Headers @{"Content-Type"="application/json";"Accept"="application/json, text/event-stream"} -Body $MCP_BODY
Assert-Status "Request without x-api-key header" $r.status 401 $r.body

Write-Section "TEST 2 - POST /mcp: empty API key (expect 401)"
$r = Invoke-Http -Url $McpUrl -Method POST -Headers @{"x-api-key"="";"Content-Type"="application/json";"Accept"="application/json, text/event-stream"} -Body $MCP_BODY
Assert-Status "Request with empty x-api-key value" $r.status 401 $r.body

Write-Section "TEST 3 - POST /mcp: wrong API key (expect 401)"
$r = Invoke-Http -Url $McpUrl -Method POST -Headers @{"x-api-key"="wrongkey-invalid-12345";"Content-Type"="application/json";"Accept"="application/json, text/event-stream"} -Body $MCP_BODY
Assert-Status "Request with incorrect x-api-key" $r.status 401 $r.body

Write-Section "TEST 4 - POST /mcp: correct API key (expect 200 + MCP handshake)"
$r = Invoke-Http -Url $McpUrl -Method POST -Headers @{"x-api-key"=$ApiKey;"Content-Type"="application/json";"Accept"="application/json, text/event-stream"} -Body $MCP_BODY
Assert-Status "Full MCP initialize with valid x-api-key" $r.status 200 $r.body
if ($r.status -eq 200) {
    if ($r.body -match '"protocolVersion"') { Write-Host "         MCP handshake: protocolVersion confirmed" -ForegroundColor DarkGreen }
    if ($r.body -match '"serverInfo"')      { Write-Host "         MCP handshake: serverInfo confirmed" -ForegroundColor DarkGreen }
}

Write-Section "TEST 5 - POST /mcp: correct key, GET method (expect not 401)"
$r = Invoke-Http -Url $McpUrl -Method GET -Headers @{"x-api-key"=$ApiKey;"Accept"="application/json, text/event-stream"}
$pass5 = ($r.status -ne 401)
if ($pass5) {
    Write-Host "  [PASS] Auth passed; FastMCP rejected wrong method" -ForegroundColor Green
    Write-Host "         Status: $($r.status) (not 401 - auth did not block this)" -ForegroundColor DarkGreen
    $PASS++
} else {
    Write-Host "  [FAIL] GET /mcp with valid key" -ForegroundColor Red
    Write-Host "         Status: $($r.status) (expected any status other than 401)" -ForegroundColor DarkRed
    $FAIL++
}

# ── New GET endpoints ────────────────────────────────────────────────────────

Write-Section "TEST 6 - GET /health: no key (expect 200 - exempt from auth)"
$r = Invoke-Http -Url "${BaseUrl}/health" -Method GET
Assert-Status "GET /health without x-api-key (liveness probe)" $r.status 200 $r.body
if ($r.status -eq 200) {
    if ($r.body -match '"status"')      { Write-Host "         Response: status field present" -ForegroundColor DarkGreen }
    if ($r.body -match '"auth_enabled"') { Write-Host "         Response: auth_enabled field present" -ForegroundColor DarkGreen }
}

Write-Section "TEST 7 - GET /info: no key (expect 401)"
$r = Invoke-Http -Url "${BaseUrl}/info" -Method GET
Assert-Status "GET /info without x-api-key" $r.status 401 $r.body

Write-Section "TEST 8 - GET /info: correct key (expect 200)"
$r = Invoke-Http -Url "${BaseUrl}/info" -Method GET -Headers @{"x-api-key"=$ApiKey}
Assert-Status "GET /info with valid x-api-key (server capabilities)" $r.status 200 $r.body
if ($r.status -eq 200) {
    if ($r.body -match '"version"')  { Write-Host "         Capabilities: version present" -ForegroundColor DarkGreen }
    if ($r.body -match '"features"') { Write-Host "         Capabilities: features present" -ForegroundColor DarkGreen }
    if ($r.body -match '"device"')   { Write-Host "         Capabilities: device present" -ForegroundColor DarkGreen }
}

# ── Summary ──────────────────────────────────────────────────────────────────
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
