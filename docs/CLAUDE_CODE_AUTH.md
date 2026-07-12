# Claude Code Auth Recovery

WeChatAI can use Claude Code as an optional read-only planner for development
tasks. The core WeChat agent, records search, RAG knowledge base, Open API, and
daily summaries do not depend on Claude Code.

## Current Failure Mode

If `claude auth status` says the account is logged in but `claude -p` returns
`401 Invalid authentication credentials`, check:

```powershell
claude auth status
claude -p "reply with exactly OK" --model sonnet --max-budget-usd 0.05
```

On this machine the stale token was in:

```text
C:\Users\qrrwi\.claude\.credentials.json
```

The token had expired and did not include a refresh token. In that state, the
CLI must be re-authorized by the user.

## Repair

Run:

```powershell
scripts\repair_claude_code_auth.ps1 -EnablePlannerAfterSuccess
```

The script:

- removes stale `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, and
  `ANTHROPIC_API_KEY` overrides from `~\.claude\settings.json`;
- backs up expired non-refreshable Claude credentials;
- starts `claude auth login --claudeai`;
- validates `claude -p "reply with exactly OK"`;
- re-enables the WeChatAI Claude Code planner only after validation succeeds.

If the login step opens a browser or asks for account confirmation, complete it
with the Claude account that owns the Max subscription.

## Safe Degraded Mode

Until Claude Code auth is refreshed, keep the planner disabled:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8090/api/agent/config" `
  -ContentType "application/json" `
  -Body '{"claude_code_planner_enabled":false}'
```

Development mode still works through the normal WeChatAI agent and the
OpenAI-compatible model provider.
