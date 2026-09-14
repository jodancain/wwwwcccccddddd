# OpenClaw direct relay

The Weixin channel uses OpenClaw only for transport. OpenClaw's model provider
is configured to call the local WeChatAI Anthropic-compatible relay:

`http://127.0.0.1:8090/relay/v1/messages`

The relay ignores OpenClaw's model prompt and sends the latest user message to
`WechatAgentService.handle_entry_text`. Intent routing, normal Claude chat,
full-history record retrieval, RAG, development actions, and confirmation rules
therefore run inside WeChatAI.

## Configure

Run this once after installing or upgrading OpenClaw:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\configure_openclaw_direct_relay.ps1
```

`start.bat` also checks this configuration before starting the gateway.

To restart the local backend and Vite frontend as one verified operation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\restart_services.ps1
```

## Reauthorize Weixin

If outbound delivery reports `ret=-2` or `prepare failed`, refresh the Weixin
authorization and then restart the gateway:

```powershell
openclaw channels login --channel openclaw-weixin
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ensure_openclaw_gateway.ps1
```

The QR login requires confirmation in Weixin. It cannot be completed by the
backend without the account owner's approval.

## Security

`/relay/v1/*` is restricted by the same localhost middleware as `/api/*`.
Remote projects must use the API-key-protected `/open/v1/*` routes instead.
