$ErrorActionPreference = "Stop"

$projectsDir = Join-Path $env:USERPROFILE ".openclaw\npm\projects"
$pluginRoots = Get-ChildItem -Path (Join-Path $projectsDir "*\node_modules\@tencent-weixin\openclaw-weixin") -Directory -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending

if (-not $pluginRoots -or $pluginRoots.Count -eq 0) {
    throw "OpenClaw Weixin plugin was not found under $projectsDir"
}

$pluginRoot = $pluginRoots[0].FullName
$srcDir = Join-Path $pluginRoot "dist\src"

function Update-TextFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Needle,
        [Parameter(Mandatory = $true)][string]$Replacement,
        [Parameter(Mandatory = $true)][string]$Marker
    )

    $text = Get-Content -Raw -LiteralPath $Path
    if ($text.Contains($Marker)) {
        Write-Host "Already patched: $Path"
        return $false
    }
    if (-not $text.Contains($Needle)) {
        throw "Patch target not found in $Path"
    }
    $next = $text.Replace($Needle, $Replacement)
    Set-Content -LiteralPath $Path -Value $next -Encoding utf8
    Write-Host "Patched: $Path"
    return $true
}

$processMessage = Join-Path $srcDir "messaging\process-message.js"
$needle = @'
        const senderId = full.from_user_id ?? "";
        if (!textBody.trim()) {
'@
$replacement = @'
        const senderId = full.from_user_id ?? "";
        if (contextToken && senderId) {
            setContextToken(deps.accountId, senderId, contextToken);
            logger.info(`[weixin] direct WeChatAI agent cached context token for from=${senderId}`);
        }
        if (!textBody.trim()) {
'@
Update-TextFile -Path $processMessage -Needle $needle -Replacement $replacement -Marker "direct WeChatAI agent cached context token" | Out-Null

$inbound = Join-Path $srcDir "messaging\inbound.js"
$needle = @'
    const val = contextTokenStore.get(k);
    logger.debug(`getContextToken: key=${k} found=${val !== undefined} storeSize=${contextTokenStore.size}`);
    return val;
'@
$replacement = @'
    let val = contextTokenStore.get(k);
    if (val === undefined) {
        restoreContextTokens(accountId);
        val = contextTokenStore.get(k);
    }
    logger.debug(`getContextToken: key=${k} found=${val !== undefined} storeSize=${contextTokenStore.size}`);
    return val;
'@
Update-TextFile -Path $inbound -Needle $needle -Replacement $replacement -Marker "let val = contextTokenStore.get(k);" | Out-Null

$api = Join-Path $srcDir "api\api.js"
$needle = @'
    finally {
        cleanup();
    }
}
/**
 * Long-poll getUpdates. Server should hold the request until new messages or timeout.
'@
$replacement = @'
    finally {
        cleanup();
    }
}
function parseWeixinJsonResponse(label, rawText) {
    try {
        return rawText ? JSON.parse(rawText) : {};
    }
    catch {
        throw new Error(`${label}: invalid JSON response: ${rawText.slice(0, 300)}`);
    }
}
function assertWeixinSuccess(label, resp) {
    const ret = resp?.ret;
    const errcode = resp?.errcode;
    if ((ret !== undefined && ret !== 0) || (errcode !== undefined && errcode !== 0)) {
        const errmsg = resp?.errmsg ?? resp?.err_msg ?? resp?.message ?? "";
        throw new Error(`${label} failed: ret=${ret ?? ""} errcode=${errcode ?? ""} errmsg=${errmsg}`);
    }
}
/**
 * Long-poll getUpdates. Server should hold the request until new messages or timeout.
'@
Update-TextFile -Path $api -Needle $needle -Replacement $replacement -Marker "parseWeixinJsonResponse" | Out-Null

$needle = @'
export async function sendMessage(params) {
    await apiPostFetch({
        baseUrl: params.baseUrl,
        endpoint: "ilink/bot/sendmessage",
        body: JSON.stringify({ ...params.body, base_info: buildBaseInfo() }),
        token: params.token,
        timeoutMs: params.timeoutMs ?? DEFAULT_API_TIMEOUT_MS,
        label: "sendMessage",
    });
}
'@
$replacement = @'
export async function sendMessage(params) {
    const rawText = await apiPostFetch({
        baseUrl: params.baseUrl,
        endpoint: "ilink/bot/sendmessage",
        body: JSON.stringify({ ...params.body, base_info: buildBaseInfo() }),
        token: params.token,
        timeoutMs: params.timeoutMs ?? DEFAULT_API_TIMEOUT_MS,
        label: "sendMessage",
    });
    const resp = parseWeixinJsonResponse("sendMessage", rawText);
    assertWeixinSuccess("sendMessage", resp);
    return resp;
}
'@
Update-TextFile -Path $api -Needle $needle -Replacement $replacement -Marker 'parseWeixinJsonResponse("sendMessage"' | Out-Null

node --check $processMessage
node --check $inbound
node --check $api

Write-Host "OpenClaw Weixin plugin patch complete: $pluginRoot"
