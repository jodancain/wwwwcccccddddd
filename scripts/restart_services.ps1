param(
    [int]$BackendPort = 8090,
    [int]$FrontendPort = 5175,
    [int]$TimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$frontendDir = Join-Path $projectRoot "frontend"
$backendDir = Join-Path $projectRoot "backend"
$siblingBackend = Join-Path (Split-Path $projectRoot -Parent) "backend"
if (Test-Path -LiteralPath (Join-Path $siblingBackend ".env")) {
    $backendDir = $siblingBackend
}

$python = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command "python.exe" -ErrorAction Stop
    $python = $pythonCommand.Source
}
$nodeCommand = Get-Command "node.exe" -ErrorAction Stop
$vite = Join-Path $frontendDir "node_modules\vite\bin\vite.js"
if (-not (Test-Path -LiteralPath $vite)) {
    throw "Vite is not installed: $vite"
}
if (-not (Test-Path -LiteralPath (Join-Path $backendDir ".env"))) {
    throw "Backend .env is missing: $backendDir\.env"
}

function Stop-WeChatAIListener {
    param(
        [int]$Port,
        [ValidateSet("backend", "frontend")][string]$Kind
    )

    $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $connection) {
        return
    }

    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }
    $commandLine = [string]$process.CommandLine
    $expected = if ($Kind -eq "backend") { "run.py" } else { "vite" }
    if ($commandLine -notmatch [regex]::Escape($expected)) {
        throw "Port $Port is owned by an unrelated process: $commandLine"
    }

    $parentId = [int]$process.ParentProcessId
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$parentId" -ErrorAction SilentlyContinue
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    if ($parent -and [string]$parent.CommandLine -match "run\.py|vite|npm") {
        Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-HttpReady {
    param([string]$Url, [int]$Timeout)

    $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(5, $Timeout))
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-BackendDataDirectory {
    param([string]$BackendDirectory)

    $configured = "data"
    $envPath = Join-Path $BackendDirectory ".env"
    $line = Get-Content -LiteralPath $envPath |
        Where-Object { $_ -match "^\s*DATA_DIR\s*=" } |
        Select-Object -Last 1
    if ($line) {
        $configured = (($line -split "=", 2)[1]).Trim().Trim('"').Trim("'")
    }
    if ([IO.Path]::IsPathRooted($configured)) {
        return [IO.Path]::GetFullPath($configured)
    }
    return [IO.Path]::GetFullPath((Join-Path $BackendDirectory $configured))
}

Stop-WeChatAIListener -Port $BackendPort -Kind "backend"
Stop-WeChatAIListener -Port $FrontendPort -Kind "frontend"
Start-Sleep -Seconds 1

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "configure_openclaw_direct_relay.ps1") `
    -RelayBaseUrl "http://127.0.0.1:$BackendPort/relay/v1"
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw relay configuration failed with exit code $LASTEXITCODE"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_openclaw_gateway.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw gateway did not become ready"
}

$dataDir = Get-BackendDataDirectory -BackendDirectory $backendDir
$logDir = Join-Path $dataDir "service-logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$env:WECHATAI_FRONTEND_DIST = Join-Path $frontendDir "dist"
Start-Process `
    -FilePath $python `
    -ArgumentList "run.py" `
    -WorkingDirectory $backendDir `
    -RedirectStandardOutput (Join-Path $logDir "backend-$BackendPort-$stamp.out.log") `
    -RedirectStandardError (Join-Path $logDir "backend-$BackendPort-$stamp.err.log") `
    -WindowStyle Hidden | Out-Null
Remove-Item Env:WECHATAI_FRONTEND_DIST -ErrorAction SilentlyContinue

Start-Process `
    -FilePath $nodeCommand.Source `
    -ArgumentList @("`"$vite`"", "--host", "0.0.0.0", "--port", [string]$FrontendPort) `
    -WorkingDirectory $frontendDir `
    -RedirectStandardOutput (Join-Path $logDir "vite-$FrontendPort-$stamp.out.log") `
    -RedirectStandardError (Join-Path $logDir "vite-$FrontendPort-$stamp.err.log") `
    -WindowStyle Hidden | Out-Null

$backendReady = Wait-HttpReady -Url "http://127.0.0.1:$BackendPort/api/agent/status" -Timeout $TimeoutSeconds
$frontendReady = Wait-HttpReady -Url "http://127.0.0.1:$FrontendPort/" -Timeout $TimeoutSeconds
if (-not $backendReady -or -not $frontendReady) {
    throw "WeChatAI restart incomplete: backend=$backendReady frontend=$frontendReady logs=$logDir"
}

Write-Host "WeChatAI restarted successfully."
Write-Host "Frontend: http://127.0.0.1:$FrontendPort/"
Write-Host "Backend:  http://127.0.0.1:$BackendPort/"
