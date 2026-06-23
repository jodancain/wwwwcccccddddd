param(
    [switch]$SkipFrontendBuild,
    [switch]$SkipPyInstallerInstall
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$frontend = Join-Path $repo "frontend"
$portableDir = Join-Path $repo "dist\WeChatAI_Portable"
$portableZip = Join-Path $repo "dist\WeChatAI_Portable.zip"
$pyiDist = Join-Path $repo "build\pyinstaller-dist"
$pyiWork = Join-Path $repo "build\pyinstaller-work"
$pyiSpec = Join-Path $repo "build\pyinstaller-spec"

Set-Location $repo

Write-Host "== WeChatAI Windows EXE build ==" -ForegroundColor Cyan
Write-Host "Repo: $repo"

if (-not $SkipFrontendBuild) {
    Write-Host "`n[1/6] Installing frontend dependencies..." -ForegroundColor Cyan
    Push-Location $frontend
    if (Test-Path "package-lock.json") {
        npm ci
    } else {
        npm install
    }

    Write-Host "`n[2/6] Building frontend..." -ForegroundColor Cyan
    npm run build
    Pop-Location
} else {
    Write-Host "`n[1/6] Skipping frontend build." -ForegroundColor Yellow
}

if (-not (Test-Path (Join-Path $frontend "dist\index.html"))) {
    throw "frontend/dist/index.html not found. Run without -SkipFrontendBuild."
}

Write-Host "`n[3/6] Installing backend dependencies..." -ForegroundColor Cyan
python -m pip install -r "$repo\backend\requirements.txt"

if (-not $SkipPyInstallerInstall) {
    Write-Host "`n[4/6] Ensuring PyInstaller is installed..." -ForegroundColor Cyan
    python -m pip install --upgrade pyinstaller
} else {
    Write-Host "`n[4/6] Skipping PyInstaller install." -ForegroundColor Yellow
}

Write-Host "`n[5/6] Building one-file EXE..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $pyiDist, $pyiWork, $pyiSpec | Out-Null

python -m PyInstaller `
    --noconfirm `
    --clean `
    --name WeChatAI `
    --onefile `
    --console `
    --paths "$repo\backend" `
    --add-data "$repo\frontend\dist;frontend\dist" `
    --add-data "$repo\.env.example;." `
    --distpath "$pyiDist" `
    --workpath "$pyiWork" `
    --specpath "$pyiSpec" `
    "$repo\packaging\wechatai_launcher.py"

Write-Host "`n[6/6] Assembling portable folder..." -ForegroundColor Cyan
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ProcessName -like "WeChatAI*" -and $_.Path -like "$portableDir*" } |
    Stop-Process -Force -ErrorAction SilentlyContinue

if (Test-Path $portableDir) {
    Remove-Item -Recurse -Force $portableDir
}
New-Item -ItemType Directory -Force -Path $portableDir | Out-Null

Copy-Item (Join-Path $pyiDist "WeChatAI.exe") (Join-Path $portableDir "WeChatAI.exe") -Force
Copy-Item (Join-Path $repo ".env.example") (Join-Path $portableDir ".env.example") -Force
Copy-Item (Join-Path $repo "docs\WINDOWS_PORTABLE_GUIDE.md") (Join-Path $portableDir "USER_GUIDE.md") -Force

@"
@echo off
chcp 65001 >nul
start "" "%~dp0WeChatAI.exe"
"@ | Set-Content -Path (Join-Path $portableDir "Start-WeChatAI.bat") -Encoding UTF8

if (Test-Path $portableZip) {
    Remove-Item -Force $portableZip
}
Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $portableZip -Force

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Portable folder: $portableDir"
Write-Host "Portable zip:    $portableZip"
Write-Host "Run: $portableDir\WeChatAI.exe"
