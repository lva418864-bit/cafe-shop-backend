<#
.SYNOPSIS
    One-time database provisioning script for Railway PostgreSQL (PowerShell).
.DESCRIPTION
    Creates the isolated microservice databases inside Railway's single PostgreSQL service:
      - user_service_db
      - operation_service_db
      - report_service_db

    Runs psql inside a throwaway Docker container (postgres:16-alpine) so no PostgreSQL
    client tools are required on the host machine.

    Reads the public TCP connection string from $env:RAILWAY_PG_PUBLIC_URL.
    For security, the URL and password are NEVER printed to the terminal or logs.

.EXAMPLE
    $env:RAILWAY_PG_PUBLIC_URL = "postgresql://postgres:secret@monorail.proxy.rlwy.net:12345/railway"
    .\scripts\railway\create-databases.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

Write-Host "======================================================================" -ForegroundColor Yellow
Write-Host " RAILWAY POSTGRESQL ONE-TIME DATABASE PROVISIONING                    " -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Yellow

$pgUrl = $env:RAILWAY_PG_PUBLIC_URL
if ([string]::IsNullOrWhiteSpace($pgUrl)) {
    Write-Host "[ERROR] Environment variable RAILWAY_PG_PUBLIC_URL is not set!" -ForegroundColor Red
    Write-Host "To set it in your current PowerShell session:" -ForegroundColor Cyan
    Write-Host '  $env:RAILWAY_PG_PUBLIC_URL = "postgresql://postgres:<password>@<host>:<port>/railway"' -ForegroundColor White
    Write-Host "`nYou can copy this connection string from Railway:" -ForegroundColor Cyan
    Write-Host "  1. Open your Railway project dashboard."
    Write-Host "  2. Click your Postgres service -> 'Connect' tab -> 'Public Network' section."
    Write-Host "  3. Copy the 'Database URL' (or TCP Proxy URL)."
    Write-Host "`nNote: Turn off the Public Network TCP Proxy in Railway after setup to save egress costs." -ForegroundColor Cyan
    exit 1
}

# Quick check that Docker is available
$dockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] Docker is not found in PATH. Please start Docker Desktop." -ForegroundColor Red
    exit 1
}

Write-Host "`nConnecting to Railway PostgreSQL via throwaway container (postgres:16-alpine)..." -ForegroundColor Cyan
Write-Host "Creating databases (idempotent; existing data is never dropped)..." -ForegroundColor Cyan

# SQL script to safely create the three application databases if they do not already exist
$initSql = @'
SELECT 'CREATE DATABASE user_service_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'user_service_db')\gexec

SELECT 'CREATE DATABASE operation_service_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'operation_service_db')\gexec

SELECT 'CREATE DATABASE report_service_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'report_service_db')\gexec

SELECT datname AS created_or_existing_database
FROM pg_database
WHERE datname IN ('user_service_db', 'operation_service_db', 'report_service_db')
ORDER BY datname;
'@

# Execute psql inside throwaway container without printing the connection string
try {
    $initSql | & $dockerBin run --rm -i -e "PGCONN=$pgUrl" postgres:16-alpine sh -c 'psql "$PGCONN" -v ON_ERROR_STOP=1'
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[FAIL] Database initialization failed. Please check network connectivity and credentials." -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "`n[FAIL] Failed to execute psql container: $_" -ForegroundColor Red
    exit 1
}

Write-Host "`n======================================================================" -ForegroundColor Green
Write-Host " [SUCCESS] All 3 application databases are ready in Railway PostgreSQL!" -ForegroundColor Green
Write-Host "   - user_service_db" -ForegroundColor Green
Write-Host "   - operation_service_db" -ForegroundColor Green
Write-Host "   - report_service_db" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green
Write-Host "`n[RECOMMENDED STEP] You can now turn off the public TCP Proxy on your Postgres" -ForegroundColor Yellow
Write-Host "service in Railway ('Settings' -> 'Networking' -> remove public TCP proxy)" -ForegroundColor Yellow
Write-Host "to prevent network egress charges. Your services will communicate over the" -ForegroundColor Yellow
Write-Host "encrypted private network exclusively." -ForegroundColor Yellow

exit 0
