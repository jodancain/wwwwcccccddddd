# WeChatAI Open API

This project exposes a local Open API so other projects can talk to the same
WeChatAI agent, records database, and knowledge base that the WeixinClawBot
entry uses.

## Base URLs

- Local machine: `http://127.0.0.1:8090`
- LAN or Tailscale: `http://<tailscale-ip-or-hostname>:8090`

For Tailscale usage, keep the service private to your tailnet and use an API key
with only the permissions that the caller needs.

The backend may listen on `0.0.0.0` for Tailscale, but `/api/*` remains
local-only by default. Remote callers should use `/open/v1/*`, which is guarded
by API-key permissions.

## Authentication

Create an external API key from the local API management endpoint or UI. Do not
commit real keys to Git.

Preferred header:

```bash
Authorization: Bearer <api_key>
```

Query-string fallback:

```text
?api_key=<api_key>
```

Useful permission sets:

- `records:read`: read synced WeChat records.
- `knowledge:read`: read/search the RAG knowledge base.
- `knowledge:write`: trigger enrichment, indexing, and embedding maintenance.
- `agent:chat`: chat with the same agent used by WeixinClawBot.
- `agent:confirm`: confirm or cancel pending agent actions.
- `project:read`: read project capability and runtime status.
- `all`: full access.

## Project Endpoints

- `GET /open/v1/project/status`
- `GET /open/v1/project/capabilities`

Example:

```bash
curl http://127.0.0.1:8090/open/v1/project/status \
  -H "Authorization: Bearer <api_key>"
```

`GET /open/v1/project/capabilities` returns the endpoint list available to the
current key, including the permission required for each route.

## Agent Chat

Use this when another project wants to talk to the WeChatAI agent directly,
the same way you chat with it through WeixinClawBot.

- `GET /open/v1/agent/status`
- `POST /open/v1/agent/chat`
- `GET /open/v1/agent/sessions/{session_id}/messages`
- `POST /open/v1/agent/actions/{action_id}/confirm`
- `POST /open/v1/agent/actions/{action_id}/cancel`

Example:

```bash
curl http://127.0.0.1:8090/open/v1/agent/chat \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"Summarize all recent WeChat records in detail\",\"detail_level\":\"high\"}"
```

Notes:

- When you ask about "chat records" without a narrow contact or time range, the
  agent should treat it as the full synced WeChat history.
- `detail_level: "high"` asks the agent to avoid short, lossy answers.
- Destructive development actions may require `agent:confirm`, depending on the
  local agent permission mode.

## Records API

Use these endpoints for raw synced WeChat data.

- `GET /open/v1/records/conversations`
- `GET /open/v1/records/recent`
- `GET /open/v1/records/messages`
- `GET /open/v1/records/by-talker`
- `GET /open/v1/records/search`
- `GET /open/v1/records/global-search`
- `GET /open/v1/{api_id}/info`
- `GET /open/v1/{api_id}/messages`
- `GET /open/v1/{api_id}/messages/recent`
- `GET /open/v1/{api_id}/search`

Examples:

```bash
curl "http://127.0.0.1:8090/open/v1/records/recent?hours=24&limit=500" \
  -H "Authorization: Bearer <api_key>"
```

Set `hours=0` when the caller really wants the full synchronized WeChat
history. Use a practical `limit` for API consumers that cannot process hundreds
of thousands of rows at once.

```bash
curl "http://127.0.0.1:8090/open/v1/records/global-search?q=NFLX&limit=50" \
  -H "Authorization: Bearer <api_key>"
```

## Knowledge API

Use these endpoints for the RAG knowledge base. The knowledge base includes
synced messages, cached link text, and image analysis when local media and a
vision-capable model are available.

- `GET /open/v1/knowledge/status`
- `POST /open/v1/knowledge/search`
- `POST /open/v1/knowledge/enrich-now`
- `POST /open/v1/knowledge/index-now`
- `POST /open/v1/knowledge/embed-now`

Example semantic search:

```bash
curl http://127.0.0.1:8090/open/v1/knowledge/search \
  -H "Authorization: Bearer <api_key>" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"US stock Netflix NFLX investment discussion\",\"limit\":10,\"use_embedding\":true}"
```

Example recent link/image enrichment:

```bash
curl -X POST "http://127.0.0.1:8090/open/v1/knowledge/enrich-now?hours=24&limit=2000&max_links=80&max_images=20" \
  -H "Authorization: Bearer <api_key>"
```

Set `hours=0` to scan the full synchronized history. This can be slow if
`max_links` or `max_images` is high because webpages and local images are parsed
and cached.

## Local Knowledge Maintenance

The internal `/api/knowledge/*` endpoints are for local maintenance and the web
app. They are intentionally not public-authenticated routes.

- `GET /api/knowledge/status`
- `POST /api/knowledge/search`
- `POST /api/knowledge/enrich-now`
- `POST /api/knowledge/index-now`
- `POST /api/knowledge/embed-now`
- `POST /api/knowledge/rebuild`

Use rebuild carefully. It clears and recreates knowledge chunks before embedding
them again.

## Tailscale Checklist

1. Start the backend on the machine that has the WeChatAI database.
2. Set `APP_HOST=0.0.0.0` in the backend `.env` when tailnet devices need to
   reach the API. Keep `INTERNAL_API_LOCAL_ONLY=true` so only `/open/v1/*` is
   reachable remotely.
3. Make sure Windows Firewall allows the backend port only for trusted networks,
   or rely on Tailscale ACLs.
4. From another tailnet device, call:

```bash
curl http://<tailscale-hostname>:8090/open/v1/project/status \
  -H "Authorization: Bearer <api_key>"
```

5. Give each external project its own API key so access can be rotated or
   revoked independently.

## OpenClaw Weixin Access Boundary

OpenClaw is used as a Weixin delivery bridge. Keep the WeChatAI project API
behind `/open/v1/*`, and keep OpenClaw itself allowlisted so strangers or groups
cannot drive the agent/tools.

Recommended OpenClaw channel posture:

```json
{
  "channels": {
    "openclaw-weixin": {
      "dmPolicy": "allowlist",
      "allowFrom": ["<your-weixin-user-id>"],
      "groupPolicy": "allowlist",
      "groupAllowFrom": []
    },
    "telegram": {
      "dmPolicy": "allowlist",
      "allowFrom": [],
      "groupPolicy": "allowlist",
      "groupAllowFrom": []
    }
  }
}
```

Apply it with `openclaw config patch --stdin`, then run
`openclaw gateway restart` and verify with `openclaw status --deep`.
