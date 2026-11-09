<#
.SYNOPSIS
    Builds and starts the local Coffee Shop Docker stack.
.DESCRIPTION
    Launches PostgreSQL, User Service, Operation Service, Report Service,
    Kong Gateway, and Next.js frontend using docker-compose.local.yml.
    Automatically initializes .env from .env.example if missing.
#>
[CmdletBinding()]
param(
    [switch]$BuildOnly,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Coffee Shop APIM - Local Docker Stack Launch (Windows)    " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Resolve Docker CLI binary
$DockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $UserDocker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $UserDocker) {
        $DockerBin = $UserDocker
    } elseif (Test-Path $ProgDocker) {
        $DockerBin = $ProgDocker
    } else {
        Write-Error "Docker executable not found! Please install or launch Docker Desktop."
        exit 1
    }
}

# 2. Check and prepare .env file
$EnvFile = Join-Path $ProjectDir ".env"
$EnvExample = Join-Path $ProjectDir ".env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExample) {
        Write-Host "[INFO] .env not found. Creating local .env from .env.example..." -ForegroundColor Yellow
        Copy-Item $EnvExample $EnvFile
        # Generate random 32-byte secret for JWT in dev
        $RandomKey = -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Max 256) })
        (Get-Content $EnvFile) -replace "JWT_SECRET_KEY=.*", "JWT_SECRET_KEY=$RandomKey" | Set-Content $EnvFile
        Write-Host "[OK] Created .env with generated development JWT_SECRET_KEY." -ForegroundColor Green
    } else {
        Write-Error ".env.example not found in $ProjectDir"
        exit 1
    }
}

# 3. Build & Start Compose Stack
$ComposeFile = "docker-compose.local.yml"
$ComposeArgs = @("compose", "-f", $ComposeFile)

if ($BuildOnly) {
    Write-Host "[INFO] Building container images..." -ForegroundColor Cyan
    & $DockerBin @ComposeArgs build
    exit $LASTEXITCODE
}

$UpArgs = @("up", "-d")
if (-not $NoBuild) {
    $UpArgs += "--build"
}

Write-Host "[INFO] Starting local Docker stack via $ComposeFile..." -ForegroundColor Cyan
& $DockerBin @ComposeArgs @UpArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Docker compose reported an issue during startup." -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "[INFO] Checking container status..." -ForegroundColor Cyan
& $DockerBin @ComposeArgs ps

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " Stack is booting! Endpoints available once healthy:     " -ForegroundColor Green
Write-Host "   - Next.js Web Frontend:    http://localhost:3000       " -ForegroundColor White
Write-Host "   - Kong APIM Proxy Entry:   http://localhost:8000       " -ForegroundColor White
Write-Host "   - Kong Admin API (Local):  http://localhost:8001       " -ForegroundColor White
Write-Host "   - PostgreSQL (Localhost):  127.0.0.1:5432              " -ForegroundColor White
Write-Host ""
Write-Host " Next steps:                                             " -ForegroundColor Yellow
Write-Host "   1. Run migrations:  .\scripts\migrate.ps1              " -ForegroundColor Yellow
Write-Host "   2. Run smoke tests: .\scripts\smoke.ps1                " -ForegroundColor Yellow
Write-Host "   3. View live logs:  .\scripts\logs.ps1                 " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Green
