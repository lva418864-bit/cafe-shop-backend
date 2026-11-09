<#
.SYNOPSIS
    Lists status and health of all local Coffee Shop Docker stack containers.
#>
[CmdletBinding()]
param()

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
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " Coffee Shop Local Stack - Container Status               " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

& $DockerBin compose -f $ComposeFile ps
