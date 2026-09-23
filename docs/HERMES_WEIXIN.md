# Hermes Weixin integration

Hermes is the primary Weixin Agent. It owns conversation memory, intent understanding, tool use, coding actions, web research, and proactive messaging. WeChatAI remains the local data service for synchronized WeChat records, RAG/embedding search, image and link enrichment, and detailed reports.

```text
WeixinClawBot
  -> Hermes Weixin gateway
  -> Hermes Agent + tools
  -> WeChatAI MCP bridge
  -> local records / knowledge base / daily reports
```

OpenClaw is not started by the project and is not part of the active message path.

## Local paths

- Hermes state: `D:\MovedFromC\C-root\WeChatAI_dev\hermes-state`
- Hermes source/runtime: `D:\MovedFromC\C-root\WeChatAI_dev\hermes-agent`
- WeChatAI repository: `D:\MovedFromC\C-root\WeChatAI_dev\WeChatai_repo`
- WeChatAI data: `Z:\windows\WeChatAI\backend-data`

Secrets stay in the local Hermes `.env` and backend `.env`; they are not committed.

## Configure and start

Run after the WeChatAI backend is listening on port 8090:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\configure_hermes_weixin.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ensure_hermes_gateway.ps1
```

The configuration script:

- migrates the existing iLink account and context cursor into Hermes once;
- restricts direct messages to the existing owner ID and disables groups;
- configures the working Claude-compatible model gateway;
- creates a dedicated controlled WeChatAI API key;
- registers the local WeChatAI MCP server;
- installs the WeChat records skill and owner instructions;
- changes daily-summary delivery to Hermes.

The script is idempotent. Once Hermes has its own account files, it can run without the legacy OpenClaw state.

## Verification

```powershell
$env:HERMES_HOME='D:\MovedFromC\C-root\WeChatAI_dev\hermes-state'
& "$env:HERMES_HOME\bin\hermes.exe" gateway status
& "$env:HERMES_HOME\bin\hermes.exe" chat -Q -q "只回答：Hermes模型正常"
```

In Weixin, send `测试`, then ask `总结最近聊天记录`. The second request means all synchronized conversations unless an explicit contact or time range is supplied.

## Daily report delivery

WeChatAI still generates the report. `DAILY_SUMMARY_SEND_TRANSPORT_ORDER=hermes` invokes `scripts/hermes_weixin_send.py`, which uses Hermes' native iLink sender and persisted context token. The preferred delivery is a short preview plus a token-protected `https://wechat.youngtuo.win/share/...` link, with document and split-text fallbacks. The Cloudflare route only publishes `/share/*`; API, relay, and administration routes remain private.

`scripts/ensure_cloudflare_tunnel.ps1` keeps the named Cloudflare tunnel running without a visible window and installs a per-user Windows login startup entry. The tunnel credential stays in the local `cloudflared-wechat/tunnel.token` file outside the repository.
