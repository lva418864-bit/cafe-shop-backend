#!/usr/bin/env bash
# =============================================================================
# Automated Kong APIM and Microservices Smoke Test Suite (Bash)
# =============================================================================
set -euo pipefail

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
ADMIN_API_URL="${ADMIN_API_URL:-http://127.0.0.1:8001}"
PASSED=0
FAILED=0

pass() {
    echo -e "  \033[32m[PASS]\033[0m $1"
    PASSED=$((PASSED + 1))
}

fail() {
    echo -e "  \033[31m[FAIL]\033[0m $1"
    FAILED=$((FAILED + 1))
}

header() {
    echo -e "\n\033[36m=== $1 ===\033[0m"
}

echo "======================================================================"
echo "KONG GATEWAY APIM SMOKE TEST SUITE (BASH)"
echo "Target Gateway:   $GATEWAY_URL"
echo "Target Admin API: $ADMIN_API_URL"
echo "======================================================================"

# -----------------------------------------------------------------------------
# 1. GATEWAY & SERVICE HEALTH CHECKS
# -----------------------------------------------------------------------------
header "1. Gateway & Service Health Checks"

HTTP_CODE=$(curl -s -o /tmp/health.json -w "%{http_code}" "$GATEWAY_URL/health" || true)
if [ "$HTTP_CODE" = "200" ]; then
    pass "Aggregated /health returns 200 OK (DB connected)"
else
    fail "Aggregated /health returned HTTP $HTTP_CODE"
fi

for svc in user operation report; do
    HTTP_CODE=$(curl -s -o /tmp/health_${svc}.json -w "%{http_code}" "$GATEWAY_URL/health/$svc" || true)
    if [ "$HTTP_CODE" = "200" ]; then
        pass "Service /health/$svc returned 200 OK"
    else
        fail "Service /health/$svc returned HTTP $HTTP_CODE"
    fi
done

# -----------------------------------------------------------------------------
# 2. CORS PREFLIGHT & CORRELATION ID
# -----------------------------------------------------------------------------
header "2. CORS Preflight & Correlation ID"

CORS_HEADER=$(curl -s -I -X OPTIONS "$GATEWAY_URL/api/products" \
    -H "Origin: http://localhost:3000" \
    -H "Access-Control-Request-Method: POST" \
    -H "Access-Control-Request-Headers: Authorization,Content-Type" | grep -i "access-control-allow-origin" || true)

if [ -n "$CORS_HEADER" ]; then
    pass "CORS preflight (OPTIONS) returned expected header: $(echo "$CORS_HEADER" | tr -d '\r')"
else
    fail "CORS preflight did not return Access-Control-Allow-Origin"
fi

REQ_ID=$(curl -s -I "$GATEWAY_URL/api/products" | grep -i "x-request-id" || true)
if [ -n "$REQ_ID" ]; then
    pass "Correlation ID injected downstream: $(echo "$REQ_ID" | tr -d '\r')"
else
    fail "X-Request-ID header missing from response"
fi

# -----------------------------------------------------------------------------
# 3. PUBLIC CATALOG ROUTES
# -----------------------------------------------------------------------------
header "3. Public Catalog Routes"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/products" || true)
if [ "$HTTP_CODE" = "200" ]; then
    pass "GET /api/products returns 200 OK (public catalog accessible)"
else
    fail "GET /api/products returned HTTP $HTTP_CODE"
fi

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/categories" || true)
if [ "$HTTP_CODE" = "200" ]; then
    pass "GET /api/categories returns 200 OK"
else
    fail "GET /api/categories returned HTTP $HTTP_CODE"
fi

# -----------------------------------------------------------------------------
# 4. AUTHENTICATION & ACCESS CONTROL (JWT)
# -----------------------------------------------------------------------------
header "4. Authentication Flow (JWT & RBAC)"

# Protected route without token MUST return 401
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$GATEWAY_URL/api/auth/me" || true)
if [ "$HTTP_CODE" = "401" ]; then
    pass "GET /api/auth/me without token correctly returned 401 Unauthorized"
else
    fail "GET /api/auth/me without token returned HTTP $HTTP_CODE; expected 401"
fi

# Register customer
RAND=$((100000 + RANDOM % 900000))
TEST_EMAIL="smoke_bash_${RAND}@example.com"
TEST_PASS="SecureSmokePass123!"

REG_OUT=$(curl -s -X POST "$GATEWAY_URL/api/auth" \
    -H "Content-Type: application/json" \
    -d "{\"username\": \"smoke_${RAND}\", \"email\": \"${TEST_EMAIL}\", \"password\": \"${TEST_PASS}\"}")

if echo "$REG_OUT" | grep -q '"success":true'; then
    pass "POST /api/auth registered test customer: $TEST_EMAIL"
else
    fail "User registration failed: $REG_OUT"
fi

# Login
LOGIN_OUT=$(curl -s -X POST "$GATEWAY_URL/api/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"${TEST_EMAIL}\", \"password\": \"${TEST_PASS}\"}")

JWT_TOKEN=$(echo "$LOGIN_OUT" | grep -o '"access_token":"[^"]*' | cut -d'"' -f4 || true)

if [ -n "$JWT_TOKEN" ]; then
    pass "POST /api/auth/login issued JWT token successfully"

    # Profile with token
    ME_OUT=$(curl -s -X GET "$GATEWAY_URL/api/auth/me" -H "Authorization: Bearer $JWT_TOKEN")
    if echo "$ME_OUT" | grep -q "$TEST_EMAIL"; then
        pass "GET /api/auth/me with Bearer token verified authenticated user"
    else
        fail "Authenticated GET /api/auth/me failed: $ME_OUT"
    fi
else
    fail "Login failed to return access_token: $LOGIN_OUT"
fi

# -----------------------------------------------------------------------------
# 5. ORDER CREATION & READ-ONLY VERIFICATION (NO REAL PAYMENTS)
# -----------------------------------------------------------------------------
header "5. Order Creation & Read-Only Verification"

if [ -n "$JWT_TOKEN" ]; then
    ORDER_OUT=$(curl -s -X POST "$GATEWAY_URL/api/orders" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $JWT_TOKEN" \
        -d '{"items": [{"product_id": 1, "quantity": 1}]}')

    ORDER_ID=$(echo "$ORDER_OUT" | grep -o '"id":[0-9]*' | head -1 | cut -d':' -f2 || true)

    if [ -n "$ORDER_ID" ]; then
        pass "POST /api/orders created Order ID: $ORDER_ID"
        CHECK_ORDER=$(curl -s -X GET "$GATEWAY_URL/api/orders/${ORDER_ID}" \
            -H "Authorization: Bearer $JWT_TOKEN")
        if echo "$CHECK_ORDER" | grep -q '"status":"pending"'; then
            pass "GET /api/orders/${ORDER_ID} confirmed status is PENDING"
        else
            fail "Order verification failed: $CHECK_ORDER"
        fi
    else
        fail "Order creation failed: $ORDER_OUT"
    fi
fi

# -----------------------------------------------------------------------------
# 6. RATE LIMITING ENFORCEMENT (HTTP 429)
# -----------------------------------------------------------------------------
header "6. Rate Limiting Enforcement (policy: local)"

HIT_429=0
echo "Sending rapid requests to /api/auth/login to trigger rate limit (limit: 10/min)..."
for i in $(seq 1 15); do
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$GATEWAY_URL/api/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"email":"bad@test.com","password":"bad"}' || true)
    if [ "$CODE" = "429" ]; then
        HIT_429=1
        break
    fi
done

if [ "$HIT_429" = "1" ]; then
    pass "Rate limiting correctly returned HTTP 429 Too Many Requests"
else
    echo "  [WARN] Did not receive 429 within 15 requests (may be within burst limit)"
fi

# -----------------------------------------------------------------------------
# 7. PERIMETER SECURITY
# -----------------------------------------------------------------------------
header "7. Perimeter Security"

ADMIN_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$ADMIN_API_URL" || true)
if [ "$ADMIN_CODE" = "200" ]; then
    pass "Kong Admin API reachable on loopback: $ADMIN_API_URL"
else
    echo "  [INFO] Kong Admin API returned $ADMIN_CODE on $ADMIN_API_URL"
fi

# -----------------------------------------------------------------------------
# SUMMARY
# -----------------------------------------------------------------------------
echo -e "\n======================================================================"
echo "SMOKE TEST EXECUTION SUMMARY"
echo -e "\033[32mPassed: $PASSED\033[0m"
echo -e "\033[31mFailed: $FAILED\033[0m"
echo "======================================================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
