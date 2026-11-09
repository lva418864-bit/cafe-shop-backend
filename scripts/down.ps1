<#
.SYNOPSIS
    Stops the local Coffee Shop Docker stack while PRESERVING database volumes.
.DESCRIPTION
    Executes 'docker compose -f docker-compose.local.yml down'.
    Named volumes (postgres_data, frontend_node_modules, etc.) are strictly kept intact.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Stopping Coffee Shop Local Docker Stack                   " -ForegroundColor Cyan
Write-Host " (Volumes are PRESERVED)                                  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$DockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $UserDocker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $UserDocker) { $DockerBin = $UserDocker }
    elseif (Test-Path $ProgDocker) { $DockerBin = $ProgDocker }
}

$ComposeFile = "docker-compose.local.yml"
& $DockerBin compose -f $ComposeFile down

Write-Host "[OK] Stack stopped successfully. PostgreSQL data remains intact." -ForegroundColor Green
