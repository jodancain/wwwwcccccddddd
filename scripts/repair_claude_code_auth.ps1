param(
    [string]$BackendUrl = "http://127.0.0.1:8090",
    [string]$Email = "liuchao@eerepo.com",
    [switch]$EnablePlannerAfterSuccess
)

$ErrorActionPreference = "Stop"

function Write-Step($Message) {
    Write-Host "[ClaudeCode] $Message"
}

function Backup-File($Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $backup = "$Path.codex-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Copy-Item -LiteralPath $Path -Destination $backup -Force
    return $backup
}

$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude) {
    $candidate = Join-Path $env:USERPROFILE ".local\bin\claude.exe"
    if (Test-Path -LiteralPath $candidate) {
        $claudePath = $candidate
    } else {
        throw "Claude Code CLI was not found. Install it first, then rerun this script."
    }
} else {
    $claudePath = $claude.Source
}

Write-Step "Using Claude CLI: $claudePath"

$settingsPath = Join-Path $env:USERPROFILE ".claude\settings.json"
if (Test-Path -LiteralPath $settingsPath) {
    $backup = Backup-File $settingsPath
    $settings = Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
    if ($settings.env) {
        foreach ($name in @("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")) {
            if ($settings.env.PSObject.Properties.Name -contains $name) {
                $settings.env.PSObject.Properties.Remove($name)
            }
        }
    }
    $settings | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
    Write-Step "Cleaned Claude settings auth overrides. Backup: $backup"
}

$credentialsPath = Join-Path $env:USERPROFILE ".claude\.credentials.json"
if (Test-Path -LiteralPath $credentialsPath) {
    $credentials = Get-Content -LiteralPath $credentialsPath -Raw | ConvertFrom-Json
    $oauth = $credentials.claudeAiOauth
    if ($oauth) {
        $expiresAt = [DateTimeOffset]::FromUnixTimeMilliseconds([int64]$oauth.expiresAt).LocalDateTime
        Write-Step "Existing Claude token expires at $($expiresAt.ToString('yyyy-MM-dd HH:mm:ss'))."
        if ($expiresAt -lt (Get-Date) -and -not $oauth.refreshToken) {
            $backup = "$credentialsPath.expired-codex-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $credentialsPath -Destination $backup -Force
            Write-Step "Moved expired non-refreshable credentials to: $backup"
        }
    }
}

Write-Step "Starting Claude Code login. Finish the browser/terminal authorization if prompted."
& $claudePath auth login --claudeai --email $Email

Write-Step "Validating Claude Code model request..."
$reply = & $claudePath -p "reply with exactly OK" --model sonnet --max-budget-usd 0.05 --output-format text
if (($reply -join "`n").Trim() -ne "OK") {
    throw "Claude Code validation did not return OK. Output: $reply"
}

Write-Step "Claude Code validation passed."

if ($EnablePlannerAfterSuccess) {
    $body = @{ claude_code_planner_enabled = $true } | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$BackendUrl/api/agent/config" -ContentType "application/json" -Body $body | Out-Null

    $envPath = "C:\WeChatAI_dev\backend\.env"
    if (Test-Path -LiteralPath $envPath) {
        $text = Get-Content -LiteralPath $envPath -Raw
        if ($text -match "(?m)^AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL=") {
            $text = [regex]::Replace($text, "(?m)^AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL=.*$", "AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL=true")
        } else {
            $text += "`r`nAGENT_DEV_ENABLE_CLAUDE_CODE_TOOL=true"
        }
        Set-Content -LiteralPath $envPath -Value $text -Encoding UTF8
    }
    Write-Step "Re-enabled WeChatAI Claude Code planner."
}

Write-Step "Done."
