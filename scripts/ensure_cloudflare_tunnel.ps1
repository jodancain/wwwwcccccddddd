param(
    [string]$CloudflaredPath = "",
    [string]$TokenFile = "",
    [string]$StateDirectory = "",
    [int]$StartupTimeoutSeconds = 15,
    [switch]$SkipStartupInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$devRoot = Split-Path $projectRoot -Parent

if ([string]::IsNullOrWhiteSpace($StateDirectory)) {
    $StateDirectory = Join-Path $devRoot "cloudflared-wechat"
}
New-Item -ItemType Directory -Path $StateDirectory -Force | Out-Null
$StateDirectory = (Resolve-Path -LiteralPath $StateDirectory).Path

if ([string]::IsNullOrWhiteSpace($CloudflaredPath)) {
    $bundledPath = "D:\App\cloudflared\cloudflared.exe"
    if (Test-Path -LiteralPath $bundledPath) {
        $CloudflaredPath = $bundledPath
    }
    else {
        $command = Get-Command "cloudflared.exe" -ErrorAction SilentlyContinue
        if ($command) {
            $CloudflaredPath = $command.Source
        }
    }
}
if ([string]::IsNullOrWhiteSpace($CloudflaredPath) -or -not (Test-Path -LiteralPath $CloudflaredPath)) {
    throw "cloudflared.exe was not found. Install it or pass -CloudflaredPath."
}
$CloudflaredPath = (Resolve-Path -LiteralPath $CloudflaredPath).Path

if ([string]::IsNullOrWhiteSpace($TokenFile)) {
    $TokenFile = Join-Path $StateDirectory "tunnel.token"
}
if (-not (Test-Path -LiteralPath $TokenFile)) {
    throw "Cloudflare tunnel token file is missing: $TokenFile"
}
$TokenFile = (Resolve-Path -LiteralPath $TokenFile).Path
if ([string]::IsNullOrWhiteSpace((Get-Content -LiteralPath $TokenFile -Raw))) {
    throw "Cloudflare tunnel token file is empty: $TokenFile"
}

function Install-CloudflareLoginStartup {
    if ($SkipStartupInstall) {
        return
    }

    $startupDirectory = [Environment]::GetFolderPath("Startup")
    if ([string]::IsNullOrWhiteSpace($startupDirectory)) {
        return
    }

    $startupFile = Join-Path $startupDirectory "WeChatAI_Cloudflare_Tunnel.vbs"
    $command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -SkipStartupInstall"
    $escapedCommand = $command.Replace('"', '""')
    $content = @(
        'Set shell = CreateObject("WScript.Shell")',
        "shell.Run `"$escapedCommand`", 0, False"
    ) -join "`r`n"
    [IO.File]::WriteAllText(
        $startupFile,
        $content + "`r`n",
        [Text.UTF8Encoding]::new($false)
    )
}

function Get-WeChatAITunnelProcess {
    $processes = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.IndexOf($TokenFile, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        Sort-Object CreationDate -Descending
    return @($processes)
}

Install-CloudflareLoginStartup

$running = @(Get-WeChatAITunnelProcess)
if ($running.Count -gt 0) {
    Write-Host "[Cloudflare] Public report tunnel is already running (PID $($running[0].ProcessId))."
    exit 0
}

$logFile = Join-Path $StateDirectory "cloudflared.log"
$arguments = @(
    "tunnel",
    "--no-autoupdate",
    "--loglevel", "info",
    "--logfile", "`"$logFile`"",
    "run",
    "--token-file", "`"$TokenFile`""
)
$process = Start-Process `
    -FilePath $CloudflaredPath `
    -ArgumentList $arguments `
    -WorkingDirectory $StateDirectory `
    -WindowStyle Hidden `
    -PassThru

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(3, $StartupTimeoutSeconds))
do {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) {
        throw "Cloudflare tunnel exited during startup. See $logFile"
    }
    $running = @(Get-WeChatAITunnelProcess)
} while ($running.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)

if ($running.Count -eq 0) {
    throw "Cloudflare tunnel process was not detected before timeout. See $logFile"
}

Write-Host "[Cloudflare] Public report tunnel is running (PID $($running[0].ProcessId))."
