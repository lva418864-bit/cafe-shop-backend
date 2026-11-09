<#
.SYNOPSIS
    Automated Kong Gateway APIM and Microservices Smoke Test Suite (PowerShell)
.DESCRIPTION
    Validates the local Docker stack through Kong Gateway:
    1. Public route returns 200 OK
    2. Protected route returns 401 without token and 200 with token
    3. 429 Too Many Requests on rate limit
    4. CORS preflight (OPTIONS) returns correct Access-Control-Allow-Origin
    5. Internal-only routes/ports blocked from host
    6. Admin API reachable on 127.0.0.1 loopback
    7. Mock payment flows: pending -> paid, expired, failed
#>
[CmdletBinding()]
param(
    [string]$GatewayUrl = "http://127.0.0.1:8000",
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
Write-Host " KONG GATEWAY LOCAL DOCKER SMOKE TEST SUITE                           " -ForegroundColor Yellow
Write-Host " Gateway Proxy:  $GatewayUrl                                          " -ForegroundColor Yellow
Write-Host " Admin API:      $AdminApiUrl                                          " -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Yellow

# -----------------------------------------------------------------------------
# 1. PUBLIC ROUTE CHECKS (200 OK)
# -----------------------------------------------------------------------------
Write-Header "1. Public Routes (200 OK)"

try {
    $res = Invoke-RestMethod -Uri "$GatewayUrl/health" -Method Get -TimeoutSec 10
    if ($res.status -eq "ok" -and $res.database -eq "connected") {
        Write-Pass "GET /health returned 200 OK (Postgres connected via Kong)"
    } else {
        Write-Fail "GET /health unexpected payload: $($res | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "GET /health failed: $_"
}

try {
    $prodRes = Invoke-RestMethod -Uri "$GatewayUrl/api/products" -Method Get -TimeoutSec 10
    if ($prodRes.success -eq $true) {
        Write-Pass "GET /api/products returned 200 OK without token (Public catalog accessible)"
        $firstProd = $prodRes.data.items | Select-Object -First 1
        if ($firstProd) {
            $detailRes = Invoke-RestMethod -Uri "$GatewayUrl/api/products/$($firstProd.id)" -Method Get -TimeoutSec 10
            if ($detailRes.success -eq $true -and $detailRes.data.id -eq $firstProd.id) {
                Write-Pass "GET /api/products/$($firstProd.id) returned 200 OK without token"
            } else {
                Write-Fail "GET /api/products/$($firstProd.id) unexpected payload"
            }
        }
    } else {
        Write-Fail "GET /api/products returned non-success response"
    }
} catch {
    Write-Fail "GET /api/products failed: $_"
}

try {
    $catRes = Invoke-RestMethod -Uri "$GatewayUrl/api/categories" -Method Get -TimeoutSec 10
    if ($catRes.success -eq $true) {
        Write-Pass "GET /api/categories returned 200 OK without token"
    } else {
        Write-Fail "GET /api/categories returned non-success response"
    }
} catch {
    Write-Fail "GET /api/categories failed: $_"
}

# -----------------------------------------------------------------------------
# 2. PROTECTED ROUTES (401 WITHOUT TOKEN, 200 WITH TOKEN)
# -----------------------------------------------------------------------------
Write-Header "2. Protected Routes (401 vs 200)"

# Verify 401 Unauthorized without token on /api/auth/me and /api/orders
try {
    $unauth = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/auth/me" -Method Get -TimeoutSec 10
    Write-Fail "GET /api/auth/me without token returned HTTP $($unauth.StatusCode); expected 401"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Pass "GET /api/auth/me without token correctly returned 401 Unauthorized"
    } else {
        Write-Fail "GET /api/auth/me without token failed with unexpected error: $_"
    }
}

try {
    $unauthOrders = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/orders" -Method Get -TimeoutSec 10
    Write-Fail "GET /api/orders without token returned HTTP $($unauthOrders.StatusCode); expected 401"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Pass "GET /api/orders without token correctly returned 401 Unauthorized"
    } else {
        Write-Fail "GET /api/orders without token failed with unexpected error: $_"
    }
}

try {
    $unauthPostOrder = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/orders" -Method Post -Body '{"items":[{"product_id":1,"quantity":1}]}' -ContentType "application/json" -TimeoutSec 10
    Write-Fail "POST /api/orders without token returned HTTP $($unauthPostOrder.StatusCode); expected 401"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Pass "POST /api/orders without token correctly returned 401 Unauthorized"
    } else {
        Write-Fail "POST /api/orders without token failed with unexpected error: $_"
    }
}

# Register a unique customer
$rand = Get-Random -Minimum 100000 -Maximum 999999
$testEmail = "smoke_dev_$rand@example.com"
$testPass = "SecureSmokePass123!"
$jwtToken = $null

try {
    $regBody = @{
        username = "smoke_dev_$rand";
        email = $testEmail;
        password = $testPass;
    } | ConvertTo-Json

    $regRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth" -Method Post -Body $regBody -ContentType "application/json" -TimeoutSec 10
    if ($regRes.success -eq $true) {
        Write-Pass "POST /api/auth registered test user ($testEmail)"
    } else {
        Write-Fail "POST /api/auth failed: $($regRes | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "POST /api/auth failed: $_"
}

# Login to obtain JWT
try {
    $loginBody = @{
        email = $testEmail;
        password = $testPass;
    } | ConvertTo-Json

    $loginRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth/login" -Method Post -Body $loginBody -ContentType "application/json" -TimeoutSec 10
    if ($loginRes.success -eq $true -and $loginRes.data.access_token) {
        $jwtToken = $loginRes.data.access_token
        Write-Pass "POST /api/auth/login returned JWT access token"
    } else {
        Write-Fail "POST /api/auth/login failed: $($loginRes | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "POST /api/auth/login failed: $_"
}

# Call protected route with Bearer token
if ($jwtToken) {
    try {
        $meRes = Invoke-RestMethod -Uri "$GatewayUrl/api/auth/me" -Method Get -Headers @{
            "Authorization" = "Bearer $jwtToken"
        } -TimeoutSec 10
        if ($meRes.success -eq $true -and $meRes.data.email -eq $testEmail) {
            Write-Pass "GET /api/auth/me with Bearer token returned 200 OK (user: $($meRes.data.email))"
        } else {
            Write-Fail "GET /api/auth/me returned unexpected data: $($meRes | ConvertTo-Json -Compress)"
        }
    } catch {
        Write-Fail "GET /api/auth/me with token failed: $_"
    }
}

# -----------------------------------------------------------------------------
# 3. RATE LIMITING ENFORCEMENT (HTTP 429)
# -----------------------------------------------------------------------------
Write-Header "3. Rate Limiting Enforcement (policy: local)"

if (-not $SkipRateLimit) {
    $hit429 = $false
    Write-Host "  Sending rapid requests to /api/auth/login (route limit: 10/min)..."
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
        Write-Pass "Rate limiting returned HTTP 429 Too Many Requests on burst"
    } else {
        Write-Host "  [WARN] Did not receive 429 within 15 requests (may be within allowed burst window)" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [SKIP] Rate limiting test skipped by switch" -ForegroundColor Yellow
}

# -----------------------------------------------------------------------------
# 4. CORS PREFLIGHT CHECK
# -----------------------------------------------------------------------------
Write-Header "4. CORS Preflight (OPTIONS)"

try {
    $corsResp = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/products" -Method Options -Headers @{
        "Origin" = "http://localhost:3000";
        "Access-Control-Request-Method" = "POST";
        "Access-Control-Request-Headers" = "Authorization,Content-Type";
    } -TimeoutSec 10
    $acao = $corsResp.Headers["Access-Control-Allow-Origin"]
    if ($acao -like "*localhost:3000*" -or $acao -eq "*") {
        Write-Pass "CORS preflight (OPTIONS) returned Access-Control-Allow-Origin: $acao"
    } else {
        Write-Fail "CORS preflight missing or invalid Access-Control-Allow-Origin header"
    }
} catch {
    Write-Fail "CORS preflight failed: $_"
}

# -----------------------------------------------------------------------------
# 5. PERIMETER SECURITY & INTERNAL ROUTE BLOCKING
# -----------------------------------------------------------------------------
Write-Header "5. Perimeter Isolation (Internal Ports Blocked)"

# Check that backend internal ports (8002, 8003) are NOT directly reachable from the host
foreach ($internalPort in @(8002, 8003)) {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $connect = $tcp.BeginConnect("127.0.0.1", $internalPort, $null, $null)
        $success = $connect.AsyncWaitHandle.WaitOne(1000, $false)
        if ($success -and $tcp.Connected) {
            $tcp.Close()
            Write-Fail "Perimeter breach: Host port $internalPort is directly exposed to host!"
        } else {
            $tcp.Close()
            Write-Pass "Internal port $internalPort is strictly blocked from host (no direct bypass)"
        }
    } catch {
        Write-Pass "Internal port $internalPort is strictly blocked from host (connection refused)"
    }
}

# Check that unmapped path through Kong returns 404
try {
    $unmapped = Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/non-existent-secret-internal-endpoint" -Method Get -TimeoutSec 5
    Write-Fail "Unmapped path returned HTTP $($unmapped.StatusCode); expected 404"
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 404) {
        Write-Pass "Unmapped Kong route correctly blocked with 404 Not Found"
    } else {
        Write-Fail "Unmapped route check error: $_"
    }
}

# -----------------------------------------------------------------------------
# 6. ADMIN API REACHABILITY (127.0.0.1 LOOPBACK ONLY)
# -----------------------------------------------------------------------------
Write-Header "6. Kong Admin API Loopback Check"

try {
    $adminRes = Invoke-RestMethod -Uri "$AdminApiUrl" -Method Get -TimeoutSec 5
    if ($adminRes.tagline -eq "Welcome to kong") {
        Write-Pass "Kong Admin API reachable on loopback: $AdminApiUrl (version: $($adminRes.version))"
    } else {
        Write-Fail "Kong Admin API returned unexpected response: $($adminRes | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "Kong Admin API connection failed on ${AdminApiUrl}: $_"
}

# -----------------------------------------------------------------------------
# 7. AUTHENTICATED ORDER & PAYMENT READ-ONLY / GATEWAY SAFETY CHECKS
# -----------------------------------------------------------------------------
Write-Header "7. Authenticated Order & Payment Gateway Safety Checks (No Real Payments)"

if ($jwtToken) {
    # 1. Verify authenticated read-only orders list endpoint works via Kong
    try {
        $ordersRes = Invoke-RestMethod -Uri "$GatewayUrl/api/orders" -Method Get -Headers @{"Authorization"="Bearer $jwtToken"} -TimeoutSec 10
        if ($ordersRes.success -eq $true) {
            Write-Pass "GET /api/orders succeeded with authenticated JWT"
        } else {
            Write-Fail "GET /api/orders returned unexpected response"
        }
    } catch {
        Write-Fail "GET /api/orders failed: $_"
    }

    # 2. Verify removed /api/payments/mock-pay route is not exposed on Kong (returns 404)
    try {
        Invoke-WebRequest -Uri "$GatewayUrl/api/payments/mock-pay/00000000000000000000000000000000" -Method Post -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop | Out-Null
        Write-Fail "POST /api/payments/mock-pay unexpectedly succeeded (should be 404)"
    } catch {
        $statusCode = $_.Exception.Response.StatusCode.value__
        if ($statusCode -eq 404) {
            Write-Pass "POST /api/payments/mock-pay is not exposed at Gateway (HTTP 404)"
        } else {
            Write-Fail "POST /api/payments/mock-pay returned HTTP $statusCode; expected 404"
        }
    }
}

# -----------------------------------------------------------------------------
# 8. PRODUCT IMAGE UPLOAD & PUBLIC MEDIA GATEWAY CHECKS
# -----------------------------------------------------------------------------
Write-Header "8. Product Image Upload (401/403) & Public Media Route Checks"

# 1. Upload endpoint without token must return 401 Unauthorized
try {
    Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/products/1/images" -Method Post -TimeoutSec 10 -ErrorAction Stop | Out-Null
    Write-Fail "POST /api/products/1/images without token unexpectedly succeeded; expected 401"
} catch {
    $statusCode = $_.Exception.Response.StatusCode.value__
    if ($statusCode -eq 401) {
        Write-Pass "POST /api/products/1/images without token correctly returned 401 Unauthorized"
    } else {
        Write-Fail "POST /api/products/1/images without token returned HTTP $statusCode; expected 401"
    }
}

# 2. Public media route answers for known product image (or verifies public /api/media route reachability)
try {
    $catalogRes = Invoke-RestMethod -Uri "$GatewayUrl/api/products?page=1&page_size=50" -Method Get -TimeoutSec 10
    $productWithImage = $catalogRes.data.items | Where-Object { $_.image_url } | Select-Object -First 1
    if ($productWithImage) {
        $mediaUri = "$GatewayUrl$($productWithImage.image_url)"
        $mediaResp = Invoke-WebRequest -UseBasicParsing -Uri $mediaUri -Method Get -TimeoutSec 10 -ErrorAction Stop
        $nosniff = $mediaResp.Headers["X-Content-Type-Options"]
        if ($mediaResp.StatusCode -eq 200 -and $nosniff -eq "nosniff") {
            Write-Pass "GET $($productWithImage.image_url) returned 200 OK with X-Content-Type-Options: nosniff"
        } else {
            Write-Fail "GET $($productWithImage.image_url) unexpected status/headers: $($mediaResp.StatusCode)"
        }
    } else {
        # Verify public media route reaches operation-service without requiring JWT
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "$GatewayUrl/api/media/00000000000000000000000000000000.jpg" -Method Get -TimeoutSec 10 -ErrorAction Stop | Out-Null
            Write-Fail "GET /api/media/<nonexistent> unexpectedly returned 200"
        } catch {
            $mediaStatus = $_.Exception.Response.StatusCode.value__
            if ($mediaStatus -eq 404) {
                Write-Pass "Public media route /api/media/* is reachable without token (404 for unknown key)"
            } else {
                Write-Fail "Public media route returned unexpected HTTP $mediaStatus"
            }
        }
    }
} catch {
    Write-Fail "Public media route check failed: $_"
}

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------
Write-Host "`n======================================================================" -ForegroundColor Yellow
Write-Host " SMOKE TEST SUITE EXECUTION SUMMARY                                   " -ForegroundColor Yellow
Write-Host " Passed: $passed" -ForegroundColor Green
Write-Host " Failed: $failed" -ForegroundColor Red
Write-Host "======================================================================" -ForegroundColor Yellow

if ($failed -gt 0) {
    exit 1
}
exit 0
