param(
    [string]$ListenAddr = "127.0.0.1:5201",
    [string]$WeChatPath = "D:\App\Weixin\Weixin.exe"
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$wetraceExe = Join-Path $repoRoot "tools\wetrace-v0.1.0\wetrace\wetrace.exe"
if (!(Test-Path -LiteralPath $wetraceExe)) {
    throw "wetrace.exe not found: $wetraceExe"
}

$workDir = Join-Path $repoRoot "data\wetrace-capture"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null

$envPath = Join-Path $workDir ".env"
$envLines = @(
    "LISTEN_ADDR=$ListenAddr",
    "WORK_DIR=data",
    "WXKEY_WECHAT_PATH=$WeChatPath"
)
Set-Content -LiteralPath $envPath -Value $envLines -Encoding UTF8

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
$isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if ($isAdmin) {
    $process = Start-Process -FilePath $wetraceExe -WorkingDirectory $workDir -WindowStyle Hidden -PassThru
} else {
    $process = Start-Process -FilePath $wetraceExe -WorkingDirectory $workDir -Verb RunAs -PassThru
}

Write-Host "Wetrace key capture started."
Write-Host "PID: $($process.Id)"
Write-Host "URL: http://$ListenAddr"
Write-Host ""
Write-Host "Open the URL, click the database key capture control, then log in to Weixin when prompted."
Write-Host "After Wetrace shows a 64-character key, run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\import_wx_db_key.ps1 -Key <64-hex-key>"
