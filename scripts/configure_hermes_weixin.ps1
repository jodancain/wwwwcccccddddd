param(
    [string]$HermesHome = "",
    [string]$HermesSource = "",
    [string]$OpenClawState = "",
    [string]$ModelCredentialFile = "",
    [string]$BackendEnv = "",
    [string]$WeChatAIBaseUrl = "http://127.0.0.1:8090",
    [string]$ModelBaseUrl = "https://ezr.sh/v1",
    [string]$ModelName = "claude-opus-4-8"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$devRoot = Split-Path $projectRoot -Parent
if (-not $HermesHome) { $HermesHome = Join-Path $devRoot "hermes-state" }
if (-not $HermesSource) { $HermesSource = Join-Path $devRoot "hermes-agent" }
if (-not $OpenClawState) { $OpenClawState = Join-Path $devRoot "openclaw-state" }
if (-not $ModelCredentialFile) { $ModelCredentialFile = Join-Path $HOME "hermes\.env" }
if (-not $BackendEnv) { $BackendEnv = Join-Path $devRoot "backend\.env" }

$hermesCli = Join-Path $HermesHome "bin\hermes.exe"
$hermesPython = Join-Path $HermesSource "venv\Scripts\python.exe"
$hermesEnv = Join-Path $HermesHome ".env"
$hermesConfig = Join-Path $HermesHome "config.yaml"
$mcpScript = Join-Path $projectRoot "scripts\wechatai_mcp_server.py"
$sendScript = Join-Path $projectRoot "scripts\hermes_weixin_send.py"
$sourceAccounts = Join-Path $OpenClawState "openclaw-weixin\accounts"

foreach ($required in @($hermesCli, $hermesPython, $hermesEnv, $hermesConfig, $mcpScript, $sendScript)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required Hermes migration path was not found: $required"
    }
}

function Get-DotEnvValue {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=(.*)$"
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match $pattern) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Set-DotEnvValue {
    param([string]$Path, [string]$Name, [string]$Value)
    if ($Value -match "[\r\n]") { throw "Environment value $Name contains a newline" }
    $lines = New-Object 'System.Collections.Generic.List[string]'
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path) { $lines.Add([string]$line) }
    }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $replacement = "$Name=$Value"
    $found = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match $pattern) {
            $lines[$index] = $replacement
            $found = $true
        }
    }
    if (-not $found) { $lines.Add($replacement) }
    [IO.File]::WriteAllLines($Path, $lines, [Text.UTF8Encoding]::new($false))
}

function Invoke-HermesConfigSet {
    param([string]$Key, [string]$Value)
    & $hermesCli config set $Key $Value --force | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Hermes rejected config value: $Key" }
}

New-Item -ItemType Directory -Path $HermesHome -Force | Out-Null
[Environment]::SetEnvironmentVariable("HERMES_HOME", $HermesHome, "User")
$env:HERMES_HOME = $HermesHome

$targetAccounts = Join-Path $HermesHome "weixin\accounts"
New-Item -ItemType Directory -Path $targetAccounts -Force | Out-Null
$accountFile = Get-ChildItem -LiteralPath $targetAccounts -File -Filter "*.json" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch "\.context-tokens\.json$|\.sync\.json$" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
$migratingFromOpenClaw = $false
if (-not $accountFile -and (Test-Path -LiteralPath $sourceAccounts)) {
    $accountFile = Get-ChildItem -LiteralPath $sourceAccounts -File -Filter "*.json" |
        Where-Object { $_.Name -notmatch "\.context-tokens\.json$|\.sync\.json$" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    $migratingFromOpenClaw = [bool]$accountFile
}
if (-not $accountFile) { throw "No existing Weixin account was found for Hermes migration" }

$account = Get-Content -LiteralPath $accountFile.FullName -Raw | ConvertFrom-Json
$token = [string]$account.token
if (-not $token) { throw "The existing Weixin account has no token" }
$accountId = ($token -split ":", 2)[0].Trim()
if (-not $accountId) { throw "Unable to derive the Weixin account ID" }

$contextSource = $accountFile.FullName -replace "\.json$", ".context-tokens.json"
$syncSource = $accountFile.FullName -replace "\.json$", ".sync.json"
$allowedUsers = @()
if (Test-Path -LiteralPath $contextSource) {
    $contextMap = Get-Content -LiteralPath $contextSource -Raw | ConvertFrom-Json
    $allowedUsers = @($contextMap.PSObject.Properties.Name | Where-Object { $_ })
}
$accountUser = [string]$(if ($account.userId) { $account.userId } else { $account.user_id })
if (-not $allowedUsers.Count -and $accountUser) { $allowedUsers = @($accountUser) }
if (-not $allowedUsers.Count) { throw "No owner Weixin user ID was found in the existing context" }
$ownerUser = [string]$allowedUsers[0]

$targetAccount = Join-Path $targetAccounts "$accountId.json"
$baseUrl = [string]$(if ($account.baseUrl) { $account.baseUrl } else { $account.base_url })
$savedAt = [string]$(if ($account.savedAt) { $account.savedAt } else { $account.saved_at })
$normalizedAccount = [ordered]@{
    token = $token
    base_url = $baseUrl
    user_id = $accountUser
    saved_at = $savedAt
}
if ($migratingFromOpenClaw) {
    [IO.File]::WriteAllText(
        $targetAccount,
        ($normalizedAccount | ConvertTo-Json -Depth 4),
        [Text.UTF8Encoding]::new($false)
    )
    if (Test-Path -LiteralPath $contextSource) {
        Copy-Item -LiteralPath $contextSource -Destination (Join-Path $targetAccounts "$accountId.context-tokens.json") -Force
    }
    if (Test-Path -LiteralPath $syncSource) {
        Copy-Item -LiteralPath $syncSource -Destination (Join-Path $targetAccounts "$accountId.sync.json") -Force
    }
}

$modelKey = Get-DotEnvValue -Path $hermesEnv -Name "EZR_API_KEY"
if (-not $modelKey) {
    $modelKey = Get-DotEnvValue -Path $ModelCredentialFile -Name "EZR_API_KEY"
}
if (-not $modelKey) { throw "EZR_API_KEY was not found in the existing Hermes credential file" }

Set-DotEnvValue -Path $hermesEnv -Name "EZR_API_KEY" -Value $modelKey
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_ACCOUNT_ID" -Value $accountId
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_TOKEN" -Value $token
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_BASE_URL" -Value $baseUrl
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_DM_POLICY" -Value "allowlist"
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_ALLOWED_USERS" -Value ($allowedUsers -join ",")
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_GROUP_POLICY" -Value "disabled"
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_HOME_CHANNEL" -Value $ownerUser
Set-DotEnvValue -Path $hermesEnv -Name "WEIXIN_HOME_CHANNEL_NAME" -Value "Owner"
Set-DotEnvValue -Path $hermesEnv -Name "WECHATAI_BASE_URL" -Value $WeChatAIBaseUrl.TrimEnd("/")

$apiKey = Get-DotEnvValue -Path $hermesEnv -Name "WECHATAI_API_KEY"
if (-not $apiKey) {
    $ready = $false
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest `
                -Uri ($WeChatAIBaseUrl.TrimEnd("/") + "/api/agent/status") `
                -UseBasicParsing `
                -TimeoutSec 4
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw "WeChatAI did not become ready at $WeChatAIBaseUrl" }

    $request = @{ name = "Hermes WeChatAI bridge"; permissions = @("all") } | ConvertTo-Json -Compress
    $created = Invoke-RestMethod `
        -Uri ($WeChatAIBaseUrl.TrimEnd("/") + "/api/chat-apis/create-agent") `
        -Method Post `
        -ContentType "application/json" `
        -Body $request `
        -TimeoutSec 30
    $apiKey = [string]$created.api_key
    if (-not $apiKey) { throw "WeChatAI did not return an API key for Hermes" }
    Set-DotEnvValue -Path $hermesEnv -Name "WECHATAI_API_KEY" -Value $apiKey
}

Invoke-HermesConfigSet -Key "providers.ezr.enabled" -Value "false"
& $hermesCli config unset "providers.ezr" | Out-Null
Invoke-HermesConfigSet -Key "providers.ezr.api" -Value $ModelBaseUrl
Invoke-HermesConfigSet -Key "providers.ezr.key_env" -Value "EZR_API_KEY"
Invoke-HermesConfigSet -Key "providers.ezr.transport" -Value "chat_completions"
Invoke-HermesConfigSet -Key "providers.ezr.default_model" -Value $ModelName
Invoke-HermesConfigSet -Key "providers.ezr.models" -Value "['$ModelName']"
Invoke-HermesConfigSet -Key "providers.ezr.discover_models" -Value "false"
Invoke-HermesConfigSet -Key "model.provider" -Value "ezr"
Invoke-HermesConfigSet -Key "model.default" -Value $ModelName
Invoke-HermesConfigSet -Key "model.base_url" -Value $ModelBaseUrl
# The EZR Claude 4.8 route chooses adaptive thinking itself. Sending Hermes'
# generic reasoning_effort makes the gateway synthesize an invalid top_p.
Invoke-HermesConfigSet -Key "model_overrides.custom.$ModelName.supports_reasoning" -Value "false"
Invoke-HermesConfigSet -Key "model_overrides.ezr.$ModelName.supports_reasoning" -Value "false"
Invoke-HermesConfigSet -Key "agent.reasoning_effort" -Value "none"
& $hermesCli config unset "agent.reasoning_effort" | Out-Null
Invoke-HermesConfigSet -Key "platform_toolsets.weixin" -Value '["hermes-weixin"]'
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.command" -Value $hermesPython
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.args" -Value "['$mcpScript']"
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.env.WECHATAI_BASE_URL" -Value $WeChatAIBaseUrl.TrimEnd("/")
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.env.WECHATAI_API_KEY" -Value '${WECHATAI_API_KEY}'
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.timeout" -Value "900"
Invoke-HermesConfigSet -Key "mcp_servers.wechatai.connect_timeout" -Value "30"

Copy-Item -LiteralPath (Join-Path $projectRoot "hermes\USER.md") -Destination (Join-Path $HermesHome "USER.md") -Force
$skillTarget = Join-Path $HermesHome "skills\wechatai-records"
New-Item -ItemType Directory -Path $skillTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "hermes\skills\wechatai-records\SKILL.md") -Destination (Join-Path $skillTarget "SKILL.md") -Force

if (Test-Path -LiteralPath $BackendEnv) {
    Set-DotEnvValue -Path $BackendEnv -Name "HERMES_HOME" -Value $HermesHome
    Set-DotEnvValue -Path $BackendEnv -Name "HERMES_CLI_PATH" -Value $hermesCli
    Set-DotEnvValue -Path $BackendEnv -Name "HERMES_PYTHON" -Value $hermesPython
    Set-DotEnvValue -Path $BackendEnv -Name "HERMES_SEND_SCRIPT" -Value $sendScript
    Set-DotEnvValue -Path $BackendEnv -Name "HERMES_GATEWAY_PID_FILE" -Value (Join-Path $HermesHome "gateway-wechatai.pid")
    Set-DotEnvValue -Path $BackendEnv -Name "DAILY_SUMMARY_SEND_TRANSPORT_ORDER" -Value "hermes"
}

Write-Host "[Hermes] Weixin credentials, owner allowlist, model, WeChatAI MCP, and skills are configured."
Write-Host "[Hermes] State: $HermesHome"
Write-Host "[Hermes] Account: $accountId; allowed owner count: $($allowedUsers.Count)"
