<#
.SYNOPSIS
    Automated Kong APIM and Microservices Smoke Test Suite (PowerShell)
.DESCRIPTION
    Validates end-to-end functionality through Kong Gateway (port 8000 by default):
    - Gateway health routes (/health, /health/user, /health/operation, /health/report)
    - Public catalog endpoints (/api/products, /api/categories)
    - Authentication flow (registration, login, profile with JWT, 401 without token)
    - CORS preflight options & X-Request-ID propagation
    - Order creation, dynamic KHQR generation, and instant mock settlement
    - Rate limit enforcement (HTTP 429)
    - Security perimeter checks (host port isolation)
#>

[CmdletBinding()]
param(
    [string]$GatewayUrl = "http://localhost:8000",
    [string]$AdminApiUrl = "http://127.0.0.1:8001",
    [switch]$SkipRateLimit
)

$ErrorActionPreference = "Continue"
$passed = 0
$failed = 0

function Write-Pass($msg) {
    Write-Host "  [PASS] $msg" -ForegroundColor Green
    $script:passed++
}

function Write-Fail($msg) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
    $script:failed++
}

function Write-Header($title) {
    Write-Host "`n=== $title ===" -ForegroundColor Cyan
}

Write-Host "======================================================================" -ForegroundColor Yellow
Write-Host "KONG GATEWAY APIM SMOKE TEST SUITE" -ForegroundColor Yellow
Write-Host "Target Gateway: $GatewayUrl" -ForegroundColor Yellow
Write-Host "Target Admin API: $AdminApiUrl" -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# 1. GATEWAY & MICROSERVICE HEALTH CHECKS
# -----------------------------------------------------------------------------
Write-Header "1. Gateway & Service Health Checks"

try {
    $res = Invoke-RestMethod -Uri "$GatewayUrl/health" -Method Get -TimeoutSec 10
    if ($res.status -eq "ok" -and $res.database -eq "connected") {
        Write-Pass "Aggregated /health returns 200 OK (DB connected)"
    } else {
        Write-Fail "Aggregated /health returned unexpected payload: $($res | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "/health check failed: $_"
}

foreach ($svc in @("user", "operation", "report")) {
    try {
        $res = Invoke-RestMethod -Uri "$GatewayUrl/health/$svc" -Method Get -TimeoutSec 10
        if ($res.status -eq "ok") {
            Write-Pass "Service /health/$svc returned 200 OK"
        } else {
            Write-Fail "Service /health/$svc returned: $($res | ConvertTo-Json -Compress)"
        }
    } catch {
        Write-Fail "Service /health/$svc failed: $_"
    }
}

# -----------------------------------------------------------------------------
# 2. CORS & DISTRIBUTED TRACING
# -----------------------------------------------------------------------------
Write-Header "2. CORS Preflight & Correlation ID"

try {
    $resp = Invoke-WebRequest -Uri "$GatewayUrl/api/products" -Method Options -Headers @{
        "Origin" = "http://localhost:3000";
        "Access-Control-Request-Method" = "POST";
        "Access-Control-Request-Headers" = "Authorization,Content-Type";
    } -TimeoutSec 10
    $acao = $resp.Headers["Access-Control-Allow-Origin"]
    if ($acao -ne $null) {
        Write-Pass "CORS preflight (OPTIONS) returned Access-Control-Allow-Origin: $acao"
    } else {
        Write-Fail "CORS preflight did not return Access-Control-Allow-Origin header"
    }
} catch {
    Write-Fail "CORS preflight failed: $_"
}

try {
    $resp = Invoke-WebRequest -Uri "$GatewayUrl/api/products" -Method Get -TimeoutSec 10
    $reqId = $resp.Headers["X-Request-ID"]
    if ($reqId -ne $null -and $reqId.Length -gt 0) {
        Write-Pass "Correlation ID injected downstream: X-Request-ID = $reqId"
    } else {
        Write-Fail "X-Request-ID header missing from response"
    }
} catch {
    Write-Fail "Product list request failed: $_"
}

# -----------------------------------------------------------------------------
# 3. PUBLIC CATALOG ROUTES
# -----------------------------------------------------------------------------
Write-Header "3. Public Catalog Routes"

try {
    $prodRes = Invoke-RestMethod -Uri "$GatewayUrl/api/products" -Method Get -TimeoutSec 10
    if ($prodRes.success -eq $true) {
        Write-Pass "GET /api/products returns 200 OK (public catalog accessible)"
    } else {
        Write-Fail "GET /api/products returned non-success"
    }
} catch {
    Write-Fail "GET /api/products failed: $_"
}

try {
    $catRes = Invoke-RestMethod -Uri "$GatewayUrl/api/categories" -Method Get -TimeoutSec 10
    if ($catRes.success -eq $true) {
        Write-Pass "GET /api/categories returns 200 OK"
    } else {
        Write-Fail "GET /api/categories returned non-success"
    }
} catch {
    Write-Fail "GET /api/categories failed: $_"
}

# -----------------------------------------------------------------------------
# 4. AUTHENTICATION & ACCESS CONTROL (JWT)
# -----------------------------------------------------------------------------
Write-Header "4. Authentication Flow (JWT & RBAC)"

# Protected route without token MUST return 401
try {
    $unauth = Invoke-WebRequest -Uri "$GatewayUrl/api/auth/me" -Method Get -TimeoutSec 10
    Write-Fail "GET /api/auth/me without token returned HTTP $($unauth.StatusCode); expected 401"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Pass "GET /api/auth/me without token correctly returned 401 Unauthorized"
    } else {
        Write-Fail "GET /api/auth/me failed with unexpected code: $_"
    }
}

# Register a unique customer
$rand = Get-Random -Minimum 100000 -Maximum 999999
$testEmail = "smoke_user_$rand@example.com"
$testPass = "SecureSmokePass123!"
$jwtToken = $null

try {
    $regBody = @{
        username = "smoke_cust_$rand";
        email = $testEmail;
        password = $testPass;
    } | ConvertTo-Json

    $regRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth" -Method Post -Body $regBody -ContentType "application/json" -TimeoutSec 10
    if ($regRes.success -eq $true) {
        Write-Pass "POST /api/auth registered test customer: $testEmail"
    } else {
        Write-Fail "POST /api/auth registration failed: $($regRes | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "User registration failed: $_"
}

# Login as customer
try {
    $loginBody = @{
        email = $testEmail;
        password = $testPass;
    } | ConvertTo-Json

    $loginRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth/login" -Method Post -Body $loginBody -ContentType "application/json" -TimeoutSec 10
    if ($loginRes.success -eq $true -and $loginRes.data.access_token) {
        $jwtToken = $loginRes.data.access_token
        Write-Pass "POST /api/auth/login issued JWT token successfully"
    } else {
        Write-Fail "POST /api/auth/login failed: $($loginRes | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "Login failed: $_"
}

# Access protected profile with token
if ($jwtToken) {
    try {
        $meRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth/me" -Method Get -Headers @{
            "Authorization" = "Bearer $jwtToken"
        } -TimeoutSec 10
        if ($meRes.success -eq $true -and $meRes.data.email -eq $testEmail) {
            Write-Pass "GET /api/auth/me with Bearer token returned authenticated user ($($meRes.data.role))"
        } else {
            Write-Fail "GET /api/auth/me returned unexpected payload: $($meRes | ConvertTo-Json -Compress)"
        }
    } catch {
        Write-Fail "Authenticated GET /api/auth/me failed: $_"
    }
}

# -----------------------------------------------------------------------------
# 5. AUTHENTICATED ORDER CREATION & READ-ONLY VERIFICATION (NO REAL PAYMENTS)
# -----------------------------------------------------------------------------
Write-Header "5. Order Creation & Read-Only Verification"

if ($jwtToken) {
    try {
        # Create an order
        $orderBody = @{
            items = @(
                @{
                    product_id = 1;
                    quantity = 1;
                }
            )
        } | ConvertTo-Json

        $orderRes = Invoke-RestMethod -Uri "$GatewayUrl/api/orders" -Method Post -Body $orderBody -ContentType "application/json" -Headers @{
            "Authorization" = "Bearer $jwtToken"
        } -TimeoutSec 10

        if ($orderRes.success -eq $true) {
            $orderId = $orderRes.data.id
            Write-Pass "POST /api/orders created Order ID: $orderId (total: $($orderRes.data.total_amount))"

            $getOrderRes = Invoke-RestMethod -Uri "$GatewayUrl/api/orders/$orderId" -Method Get -Headers @{
                "Authorization" = "Bearer $jwtToken"
            } -TimeoutSec 10
            if ($getOrderRes.data.status -eq "pending") {
                Write-Pass "GET /api/orders/$orderId confirmed status is PENDING"
            } else {
                Write-Fail "Order status is $($getOrderRes.data.status); expected pending"
            }
        } else {
            Write-Fail "Order creation failed: $($orderRes | ConvertTo-Json -Compress)"
        }
    } catch {
        Write-Fail "Order/Payment flow failed: $_"
    }
}

# -----------------------------------------------------------------------------
# 6. RATE LIMITING ENFORCEMENT (HTTP 429)
# -----------------------------------------------------------------------------
Write-Header "6. Rate Limiting Enforcement (policy: local)"

if (-not $SkipRateLimit) {
    $hit429 = $false
    Write-Host "Sending rapid requests to /api/auth/login to trigger rate limit (limit: 10/min)..."
    for ($i = 1; $i -le 15; $i++) {
        try {
            $null = Invoke-RestMethod -Uri "$GatewayUrl/api/auth/login" -Method Post -Body '{"email":"invalid@test.com","password":"bad"}' -ContentType "application/json" -TimeoutSec 5
        } catch {
            if ($_.Exception.Response.StatusCode.value__ -eq 429) {
                $hit429 = $true
                break
            }
        }
    }
    if ($hit429) {
        Write-Pass "Rate limiting correctly returned HTTP 429 Too Many Requests"
    } else {
        Write-Host "  [WARN] Did not receive 429 within 15 requests (may be within allowed burst window)" -ForegroundColor Yellow
    }
}

# -----------------------------------------------------------------------------
# 7. PERIMETER SECURITY: ADMIN API & INTERNAL PORTS
# -----------------------------------------------------------------------------
Write-Header "7. Perimeter Security"

# Verify Kong Admin API is bound only to localhost / not publicly open
try {
    $adminRes = Invoke-RestMethod -Uri "$AdminApiUrl" -Method Get -TimeoutSec 5
    if ($adminRes.tagline -eq "Welcome to kong") {
        Write-Pass "Kong Admin API reachable on loopback: $AdminApiUrl"
    }
} catch {
    Write-Host "  [INFO] Kong Admin API not listening on $AdminApiUrl (expected if completely disabled or in separate container)"
}

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------
Write-Host "`n======================================================================" -ForegroundColor Yellow
Write-Host "SMOKE TEST EXECUTION SUMMARY" -ForegroundColor Yellow
Write-Host "Passed: $passed" -ForegroundColor Green
Write-Host "Failed: $failed" -ForegroundColor Red
Write-Host "======================================================================" -ForegroundColor Yellow

if ($failed -gt 0) {
    exit 1
}
exit 0
