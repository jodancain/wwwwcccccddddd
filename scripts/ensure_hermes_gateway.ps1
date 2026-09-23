param(
    [string]$HermesHome = "",
    [string]$WorkingDirectory = "",
    [int]$TimeoutSeconds = 35
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$devRoot = Split-Path $projectRoot -Parent
if (-not $HermesHome) {
    $HermesHome = [Environment]::GetEnvironmentVariable("HERMES_HOME", "User")
}
if (-not $HermesHome) { $HermesHome = Join-Path $devRoot "hermes-state" }
if (-not $WorkingDirectory) { $WorkingDirectory = $projectRoot }

$hermesCli = Join-Path $HermesHome "bin\hermes.exe"
$logDir = Join-Path $HermesHome "logs"
if (-not (Test-Path -LiteralPath $hermesCli)) {
    Write-Warning "Hermes CLI was not found: $hermesCli"
    exit 2
}

function Test-HermesGateway {
    $previousHome = $env:HERMES_HOME
    try {
        $env:HERMES_HOME = $HermesHome
        $output = (& $hermesCli gateway status 2>&1 | Out-String)
        return ($LASTEXITCODE -eq 0 -and $output -notmatch "not running")
    }
    catch {
        return $false
    }
    finally {
        $env:HERMES_HOME = $previousHome
    }
}

if (Test-HermesGateway) {
    Write-Host "[Hermes] Gateway is already running."
    exit 0
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$previousHome = $env:HERMES_HOME
try {
    $env:HERMES_HOME = $HermesHome
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $hermesCli gateway start 2>&1 | Out-Null
    $startExitCode = $LASTEXITCODE
    if ($startExitCode -ne 0) {
        & $hermesCli gateway install --force --start-now --start-on-login 2>&1 | Out-Null
        $startExitCode = $LASTEXITCODE
    }
    $ErrorActionPreference = $previousErrorAction
    if ($startExitCode -ne 0) {
        Write-Warning "Hermes gateway service could not be started or installed."
        exit 3
    }
}
finally {
    $ErrorActionPreference = "Stop"
    $env:HERMES_HOME = $previousHome
}

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(5, $TimeoutSeconds))
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-HermesGateway) {
        Write-Host "[Hermes] Weixin gateway started."
        exit 0
    }
    Start-Sleep -Milliseconds 750
}

Write-Warning "Hermes gateway did not become ready. See $(Join-Path $logDir 'gateway.log')"
exit 3
