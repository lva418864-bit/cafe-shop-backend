<#
.SYNOPSIS
    Applies database migrations and initializes schemas for all microservices.
.DESCRIPTION
    Runs Alembic migrations for operation_service and ensures tables are provisioned
    for user_service and report_service inside the running Docker Compose containers.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Applying Database Migrations Across All Microservices     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$DockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $UserDocker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $UserDocker) { $DockerBin = $UserDocker }
    elseif (Test-Path $ProgDocker) { $DockerBin = $ProgDocker }
}

$ComposeFile = "docker-compose.local.yml"

# 1. Operation Service Alembic Migrations
Write-Host "[1/3] Running Alembic migrations for operation-service..." -ForegroundColor Cyan
& $DockerBin compose -f $ComposeFile exec -T operation-service alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Write-Error "Alembic migrations failed for operation-service."
    exit 1
}
Write-Host "[OK] operation_service migrations applied to head." -ForegroundColor Green

# 2. User Service Schemas
Write-Host "[2/3] Verifying user-service schema..." -ForegroundColor Cyan
& $DockerBin compose -f $ComposeFile exec -T user-service python -c "from app.core.database import Base, engine; from app.models.user import User; Base.metadata.create_all(bind=engine); print('user_service schema verified.')"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Schema initialization failed for user-service."
    exit 1
}
Write-Host "[OK] user_service schema ready." -ForegroundColor Green

# 3. Report Service Schemas
Write-Host "[3/3] Verifying report-service schema..." -ForegroundColor Cyan
& $DockerBin compose -f $ComposeFile exec -T report-service python -c "from app.core.database import Base, engine; from app.models.snapshot import OrderItemSnapshot, OrderSnapshot, ProductSnapshot, UserSnapshot; Base.metadata.create_all(bind=engine); print('report_service schema verified.')"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Schema initialization failed for report-service."
    exit 1
}
Write-Host "[OK] report_service schema ready." -ForegroundColor Green

Write-Host "==========================================================" -ForegroundColor Green
Write-Host " All service databases migrated and verified!             " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
