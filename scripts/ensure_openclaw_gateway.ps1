param(
    [int]$Port = 18789,
    [int]$TimeoutSeconds = 25
)

$ErrorActionPreference = "Stop"

function Test-OpenClawGateway {
    param([int]$TargetPort)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(400)) {
            return $false
        }
        $client.EndConnect($pending)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

if (Test-OpenClawGateway -TargetPort $Port) {
    Write-Host "[OpenClaw] Gateway already listening on $Port."
    exit 0
}

$userProfilePath = [Environment]::GetFolderPath("UserProfile")
$gatewayScript = Join-Path $userProfilePath ".openclaw\gateway.cmd"
if (-not (Test-Path -LiteralPath $gatewayScript)) {
    Write-Warning "OpenClaw gateway script not found: $gatewayScript"
    exit 2
}

$logDir = Join-Path $userProfilePath ".openclaw\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stdoutLog = Join-Path $logDir "gateway-autostart.out.log"
$stderrLog = Join-Path $logDir "gateway-autostart.err.log"

Start-Process `
    -FilePath $gatewayScript `
    -WorkingDirectory $userProfilePath `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden | Out-Null

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(3, $TimeoutSeconds))
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-OpenClawGateway -TargetPort $Port) {
        Write-Host "[OpenClaw] Gateway started on $Port."
        exit 0
    }
    Start-Sleep -Milliseconds 500
}

Write-Warning "OpenClaw gateway did not become ready on $Port. See $stderrLog"
exit 3
