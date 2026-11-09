<#
.SYNOPSIS
    Resets the local development PostgreSQL volume for the Coffee Shop stack.
.DESCRIPTION
    Stops the local compose stack and removes ONLY the 'coffee_local_postgres_data' volume.
    Requires an explicit -Confirm switch or interactive 'yes' confirmation.
    NEVER touches any host database or production volumes.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [switch]$ConfirmAction
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Red
Write-Host " DANGER: Local Development Database Reset                 " -ForegroundColor Red
Write-Host " Target Volume: coffee_local_postgres_data                " -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Red

if (-not $ConfirmAction) {
    $Prompt = Read-Host "Are you sure you want to completely erase the local PostgreSQL database? [type 'yes' to confirm]"
    if ($Prompt -ne "yes") {
        Write-Host "[ABORTED] Database reset cancelled by user." -ForegroundColor Green
        exit 0
    }
}

$DockerBin = "docker"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $UserDocker = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin\docker.exe"
    $ProgDocker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path $UserDocker) { $DockerBin = $UserDocker }
    elseif (Test-Path $ProgDocker) { $DockerBin = $ProgDocker }
}

$ComposeFile = "docker-compose.local.yml"
$TargetVolume = "coffee_local_postgres_data"

Write-Host "[1/3] Stopping local stack containers..." -ForegroundColor Cyan
& $DockerBin compose -f $ComposeFile down

Write-Host "[2/3] Removing ONLY the local Postgres volume '$TargetVolume'..." -ForegroundColor Cyan
$ExistingVol = & $DockerBin volume ls -q --filter "name=$TargetVolume"
if ($ExistingVol) {
    & $DockerBin volume rm $TargetVolume
    Write-Host "[OK] Volume '$TargetVolume' removed." -ForegroundColor Green
} else {
    Write-Host "[INFO] Volume '$TargetVolume' was not found (already clean)." -ForegroundColor Yellow
}

Write-Host "[3/3] Ready for clean reboot." -ForegroundColor Cyan
Write-Host "Run '.\scripts\up.ps1' to restart the database and stack with a fresh schema." -ForegroundColor Green
