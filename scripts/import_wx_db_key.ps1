param(
    [Parameter(Mandatory = $true)]
    [string]$Key
)

$ErrorActionPreference = "Stop"

$keyValue = $Key.Trim()
if ($keyValue -notmatch "^[0-9a-fA-F]{64}$") {
    throw "WX_DB_KEY must be exactly 64 hex characters."
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendEnv = Join-Path $repoRoot "backend\.env"

if (Test-Path -LiteralPath $backendEnv) {
    $lines = Get-Content -LiteralPath $backendEnv
} else {
    $lines = @()
}

$updated = $false
$newLines = foreach ($line in $lines) {
    if ($line -match "^\s*WX_DB_KEY\s*=") {
        $updated = $true
        "WX_DB_KEY=$keyValue"
    } else {
        $line
    }
}

if (!$updated) {
    if ($newLines.Count -gt 0 -and $newLines[-1].Trim() -ne "") {
        $newLines += ""
    }
    $newLines += "WX_DB_KEY=$keyValue"
}

Set-Content -LiteralPath $backendEnv -Value $newLines -Encoding UTF8
Write-Host "WX_DB_KEY updated in backend\.env."
Write-Host "Restart the backend for the key to take effect."
