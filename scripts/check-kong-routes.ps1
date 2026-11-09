<#
.SYNOPSIS
    Compares Kong routes between local config and Railway template to prevent route drift.
.DESCRIPTION
    Extracts route names, paths, and methods from:
      - kong/kong.yml (local Docker Compose stack)
      - infra/railway/kong/kong.template.yml (Railway production template)
    Verifies that all application routes match. Ignores the Railway-only gateway health route.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

$LocalFile = Join-Path $ProjectDir "kong\kong.yml"
$RailwayFile = Join-Path $ProjectDir "infra\railway\kong\kong.template.yml"

if (-not (Test-Path $LocalFile)) {
    Write-Error "Local kong.yml not found at: $LocalFile"
    exit 1
}
if (-not (Test-Path $RailwayFile)) {
    Write-Error "Railway kong.template.yml not found at: $RailwayFile"
    exit 1
}

function Parse-KongRoutes([string]$FilePath) {
    $content = Get-Content -Path $FilePath
    $routes = @()
    $currentRoute = $null
    $inPaths = $false
    $inMethods = $false

    foreach ($line in $content) {
        $trimmed = $line.Trim()

        if ($line -match '^\s*-\s*name:\s*([a-zA-Z0-9_-]+-route)') {
            if ($currentRoute) {
                $routes += [PSCustomObject]$currentRoute
            }
            $currentRoute = @{
                Name = $Matches[1]
                Paths = [System.Collections.Generic.List[string]]::new()
                Methods = [System.Collections.Generic.List[string]]::new()
            }
            $inPaths = $false
            $inMethods = $false
            continue
        }

        if ($currentRoute) {
            if ($line -match '^\s*paths:') {
                $inPaths = $true
                $inMethods = $false
                continue
            }
            if ($line -match '^\s*methods:') {
                $inMethods = $true
                $inPaths = $false
                continue
            }
            if ($line -match '^\s*[a-zA-Z_]+:' -and -not ($line -match '^\s*-\s*')) {
                $inPaths = $false
                $inMethods = $false
            }
            if ($inPaths -and $line -match '^\s*-\s*(\S+)') {
                $currentRoute.Paths.Add($Matches[1])
            }
            if ($inMethods -and $line -match '^\s*-\s*([A-Z]+)') {
                $currentRoute.Methods.Add($Matches[1])
            }
        }
    }

    if ($currentRoute) {
        $routes += [PSCustomObject]$currentRoute
    }

    return $routes
}

Write-Host "======================================================================" -ForegroundColor Yellow
Write-Host " KONG GATEWAY ROUTE DRIFT CHECK                                       " -ForegroundColor Yellow
Write-Host " Local config:    kong/kong.yml                                       " -ForegroundColor Yellow
Write-Host " Railway config:  infra/railway/kong/kong.template.yml                 " -ForegroundColor Yellow
Write-Host "======================================================================" -ForegroundColor Yellow

$localRoutes = Parse-KongRoutes $LocalFile
$railwayRoutes = Parse-KongRoutes $RailwayFile

# Exclude Railway-only internal gateway liveness check
$railwayAppRoutes = $railwayRoutes | Where-Object { $_.Name -ne "gateway-health-route" }

Write-Host "Found $($localRoutes.Count) local routes and $($railwayAppRoutes.Count) Railway application routes." -ForegroundColor Cyan

$driftDetected = $false

# 1. Check local routes exist in Railway
foreach ($lr in $localRoutes) {
    $matching = $railwayAppRoutes | Where-Object { $_.Name -eq $lr.Name }
    if (-not $matching) {
        Write-Host "  [MISSING IN RAILWAY] Route: $($lr.Name)" -ForegroundColor Red
        $driftDetected = $true
        continue
    }

    $localPaths = ($lr.Paths | Sort-Object) -join ","
    $railwayPaths = ($matching.Paths | Sort-Object) -join ","
    if ($localPaths -ne $railwayPaths) {
        Write-Host "  [PATH MISMATCH] Route: $($lr.Name) | Local: $localPaths != Railway: $railwayPaths" -ForegroundColor Red
        $driftDetected = $true
    }

    $localMethods = ($lr.Methods | Sort-Object) -join ","
    $railwayMethods = ($matching.Methods | Sort-Object) -join ","
    if ($localMethods -ne $railwayMethods) {
        Write-Host "  [METHOD MISMATCH] Route: $($lr.Name) | Local: $localMethods != Railway: $railwayMethods" -ForegroundColor Red
        $driftDetected = $true
    }
}

# 2. Check Railway routes exist in local
foreach ($rr in $railwayAppRoutes) {
    $matching = $localRoutes | Where-Object { $_.Name -eq $rr.Name }
    if (-not $matching) {
        Write-Host "  [EXTRA IN RAILWAY] Route: $($rr.Name)" -ForegroundColor Red
        $driftDetected = $true
    }
}

if ($driftDetected) {
    Write-Host "`n[FAIL] Kong route drift detected between local config and Railway template!" -ForegroundColor Red
    exit 1
}

Write-Host "`n[PASS] All $($localRoutes.Count) routes match exactly across local and Railway configurations." -ForegroundColor Green
exit 0
