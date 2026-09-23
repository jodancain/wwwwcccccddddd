param(
    [string]$RelayBaseUrl = "http://127.0.0.1:8090/relay/v1"
)

$ErrorActionPreference = "Stop"
$targetModel = "wechatai/wechatai-direct-agent"
$stateEntry = Get-Item -LiteralPath (Join-Path $HOME ".openclaw") -ErrorAction SilentlyContinue
$stateDir = if ($stateEntry -and $stateEntry.Target) {
    [string]@($stateEntry.Target)[0]
} else {
    Join-Path $HOME ".openclaw"
}
$runtimeTemp = Join-Path (Split-Path $stateDir -Parent) "openclaw-runtime-temp"
New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
$env:TMPDIR = $runtimeTemp
$env:TEMP = $runtimeTemp
$env:TMP = $runtimeTemp
$openClaw = Get-Command "openclaw.cmd" -ErrorAction SilentlyContinue
if (-not $openClaw) {
    $openClaw = Get-Command "openclaw" -ErrorAction SilentlyContinue
}
if (-not $openClaw) {
    Write-Warning "OpenClaw CLI was not found; direct WeChat relay was not configured."
    exit 2
}

$currentModel = (& $openClaw.Source config get agents.defaults.model.primary 2>$null | Out-String).Trim()
$providerReady = $false
try {
    $providerRaw = (& $openClaw.Source config get models.providers.wechatai 2>$null | Out-String).Trim()
    $provider = $providerRaw | ConvertFrom-Json
    $configuredModel = @($provider.models) | Where-Object { $_.id -eq "wechatai-direct-agent" } | Select-Object -First 1
    $providerReady = (
        $provider.baseUrl.TrimEnd("/") -eq $RelayBaseUrl.TrimEnd("/") -and
        $configuredModel.api -eq "anthropic-messages"
    )
}
catch {
    $providerReady = $false
}

if ($currentModel -eq $targetModel -and $providerReady) {
    Write-Host "[OpenClaw] Direct WeChatAI relay is already configured."
    exit 0
}

$operations = @(
    @{
        path = "models.providers.wechatai"
        value = @{
            baseUrl = $RelayBaseUrl
            apiKey = "local-only"
            models = @(
                @{
                    id = "wechatai-direct-agent"
                    name = "WeChatAI Direct Agent"
                    api = "anthropic-messages"
                    input = @("text")
                    contextTokens = 200000
                    maxTokens = 8192
                }
            )
        }
    },
    @{
        path = "agents.defaults.model.primary"
        value = $targetModel
    }
) | ConvertTo-Json -Depth 8 -Compress

& $openClaw.Source config set --batch-json $operations
if ($LASTEXITCODE -ne 0) {
    Write-Warning "OpenClaw rejected the direct relay configuration."
    exit $LASTEXITCODE
}

Write-Host "[OpenClaw] Direct WeChatAI relay configured as $targetModel."
exit 0
