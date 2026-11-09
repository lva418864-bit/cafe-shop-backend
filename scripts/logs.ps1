<#
.SYNOPSIS
    Tails or displays logs from local Coffee Shop Docker stack services.
.PARAMETER Service
    Optional name of a specific service (e.g., kong, user-service, operation-service, report-service, postgres, frontend).
.PARAMETER Follow
    If specified, follows the log output in real-time (-f).
.PARAMETER Tail
    Number of lines to show from the end of the logs (default: 100).
#>
[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$Service,

    [switch]$Follow,

    [int]$Tail = 100
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

$DockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $UserDocker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $UserDocker) { $DockerBin = $UserDocker }
    elseif (Test-Path $ProgDocker) { $DockerBin = $ProgDocker }
}

$ComposeFile = "docker-compose.local.yml"
$LogArgs = @("compose", "-f", $ComposeFile, "logs", "--tail=$Tail")

if ($Follow) {
    $LogArgs += "-f"
}

if ($Service) {
    $LogArgs += $Service
    Write-Host "[INFO] Displaying logs for service: $Service..." -ForegroundColor Cyan
} else {
    Write-Host "[INFO] Displaying logs for all stack services..." -ForegroundColor Cyan
}

& $DockerBin @LogArgs
