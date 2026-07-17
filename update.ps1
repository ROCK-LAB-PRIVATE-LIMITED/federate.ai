# update.ps1
# ==============================================================================
#            Federate Native Windows PowerShell Updater Script
# ==============================================================================
$ErrorActionPreference = "Stop"

param(
    [switch]$Force
)

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "          Federate Universal uv-Based Updater                          " -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan

# 1. Ensure uv is on the path for this session
$localBin = Join-Path $HOME ".local\bin"
if ($env:PATH -notlike "*$localBin*") {
    $env:PATH = "$localBin;" + $env:PATH
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not detected on your environment PATH. Please run the installer script first."
}

# 2. Get current installed version
$installedVer = $null
$toolList = uv tool list 2>$null
if ($toolList -match 'federate\s+v([\d\.]+)') {
    $installedVer = $Matches[1]
}

if (-not $installedVer) {
    try {
        $installedVer = python -c "import importlib.metadata; print(importlib.metadata.version('federate'))" 2>$null
        $installedVer = $installedVer.Trim()
    } catch {}
}

if ($installedVer) {
    Write-Host "[*] Installed Version: v$installedVer" -ForegroundColor Green
} else {
    Write-Host "[*] Installed Version: Not Detected" -ForegroundColor Yellow
}

# 3. Fetch latest version from PyPI twice (cache buster)
Write-Host "[*] Querying PyPI for the latest version..." -ForegroundColor Yellow

function Get-PyPiVersion {
    try {
        $cb = Get-Random -Minimum 1 -Maximum 1000000
        $url = "https://pypi.org/pypi/federate/json?cb=$cb"
        $response = Invoke-RestMethod -Uri $url -TimeoutSec 10 -Headers @{"User-Agent"="Mozilla/5.0"}
        return $response.info.version
    } catch {
        return $null
    }
}

$v1 = Get-PyPiVersion
Start-Sleep -Seconds 1
$v2 = Get-PyPiVersion

$latestVer = $null
if ($v1 -and $v2) {
    try {
        $p1 = [version]$v1
        $p2 = [version]$v2
        if ($p2 -ge $p1) { $latestVer = $v2 } else { $latestVer = $v1 }
    } catch {
        $latestVer = $v2
    }
} elseif ($v2) {
    $latestVer = $v2
} else {
    $latestVer = $v1
}

if ($latestVer) {
    Write-Host "[*] Latest PyPI Version: v$latestVer" -ForegroundColor Green
} else {
    Write-Host "[!] Warning: Could not retrieve latest version from PyPI automatically." -ForegroundColor Yellow
}

# 4. Compare versions
$upToDate = $false
if ($installedVer -and $latestVer -and ($installedVer -eq $latestVer)) {
    $upToDate = $true
}

if ($upToDate -and -not $Force) {
    Write-Host "======================================================================" -ForegroundColor Green
    Write-Host " ℹ️ Federate is already up-to-date (v$installedVer)." -ForegroundColor Green
    Write-Host " If you want to force-reinstall or refresh the installation, please run:" -ForegroundColor Green
    Write-Host "     .\update.ps1 -Force" -ForegroundColor Green
    Write-Host "======================================================================" -ForegroundColor Green
    exit 0
}

Write-Host "[*] Upgrading Federate on standardized Python 3.13..." -ForegroundColor Yellow
uv tool install --upgrade --python 3.13 "federate[audio,vision]"

Write-Host "======================================================================" -ForegroundColor Green
Write-Host " 🎉 Federate has been successfully updated!" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Green