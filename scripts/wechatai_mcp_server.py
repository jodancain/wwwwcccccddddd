from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server import MCPServer


BASE_URL = os.getenv("WECHATAI_BASE_URL", "http://127.0.0.1:8090").rstrip("/")
API_KEY = os.getenv("WECHATAI_API_KEY", "").strip()

server = MCPServer(
    "wechatai",
    title="WeChatAI local records and knowledge",
    description=(
        "Search and analyze all locally synchronized WeChat records, the RAG knowledge base, "
        "and daily reports. Generic references to chat history mean every synchronized conversation."
    ),
    version="1.0.0",
)


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    authenticated: bool = True,
    timeout: int = 120,
) -> Any:
    url = f"{BASE_URL}{path}"
    headers = {"Accept": "application/json"}
    if authenticated:
        if not API_KEY:
            raise RuntimeError("WECHATAI_API_KEY is not configured for the Hermes MCP bridge")
        headers["Authorization"] = f"Bearer {API_KEY}"
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"WeChatAI HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"WeChatAI is unavailable at {BASE_URL}: {exc.reason}") from exc
    return json.loads(raw) if raw else {}


def _query(path: str, **params: Any) -> str:
    clean = {key: value for key, value in params.items() if value not in (None, "")}
    suffix = urllib.parse.urlencode(clean)
    return f"{path}?{suffix}" if suffix else path


@server.tool()
def wechat_project_status() -> dict[str, Any]:
    """Check WeChatAI sync, agent, knowledge-base, embedding, and runtime status."""
    return _request("/open/v1/project/status")


@server.tool()
def list_wechat_conversations(search: str = "", limit: int = 200) -> dict[str, Any]:
    """List synchronized WeChat contacts and groups. Use this to resolve a name to a talker ID."""
    rows = _request("/open/v1/records/conversations")
    if not isinstance(rows, list):
        return {"count": 0, "items": [], "raw": rows}
    needle = search.strip().lower()
    if needle:
        rows = [
            row
            for row in rows
            if needle
            in " ".join(
                str(row.get(key) or "")
                for key in ("talker", "nickname", "remark", "display_name", "content")
            ).lower()
        ]
    safe_limit = max(1, min(int(limit), 1000))
    return {"count": min(len(rows), safe_limit), "total_matches": len(rows), "items": rows[:safe_limit]}


@server.tool()
def get_wechat_records(
    hours: int = 24,
    limit: int = 1000,
    talker: str = "",
    page: int = 1,
    search: str = "",
) -> dict[str, Any]:
    """Read raw synchronized WeChat messages. Set hours=0 for all history across all conversations."""
    safe_limit = max(1, min(int(limit), 5000))
    if talker.strip():
        return _request(
            _query(
                "/open/v1/records/by-talker",
                talker=talker.strip(),
                page=max(1, int(page)),
                page_size=min(safe_limit, 500),
                search=search.strip(),
            )
        )
    if search.strip():
        return _request(
            _query("/open/v1/records/global-search", q=search.strip(), limit=min(safe_limit, 1000))
        )
    return _request(
        _query(
            "/open/v1/records/recent",
            hours=max(0, min(int(hours), 720)),
            limit=safe_limit,
        )
    )


@server.tool()
def search_wechat_knowledge(
    query: str,
    limit: int = 12,
    talker: str = "",
    use_embedding: bool = True,
) -> dict[str, Any]:
    """Semantic RAG search over messages plus parsed image and link knowledge. Never invent missing evidence."""
    return _request(
        "/open/v1/knowledge/search",
        method="POST",
        payload={
            "query": query,
            "limit": max(1, min(int(limit), 30)),
            "talker": talker.strip(),
            "use_embedding": bool(use_embedding),
        },
    )


@server.tool()
def wechat_knowledge_status() -> dict[str, Any]:
    """Return RAG indexing, enrichment, image/link parsing, and embedding status."""
    return _request("/open/v1/knowledge/status")


@server.tool()
def enrich_wechat_knowledge(
    hours: int = 24,
    limit: int = 2000,
    max_links: int = 80,
    max_images: int = 20,
) -> dict[str, Any]:
    """Parse recent links and images into the knowledge base. Set hours=0 for all synchronized history."""
    return _request(
        _query(
            "/open/v1/knowledge/enrich-now",
            hours=max(0, min(int(hours), 720)),
            limit=max(1, min(int(limit), 50000)),
            max_links=max(0, min(int(max_links), 500)),
            max_images=max(0, min(int(max_images), 200)),
        ),
        method="POST",
        timeout=900,
    )


@server.tool()
def embed_wechat_knowledge(limit: int = 128) -> dict[str, Any]:
    """Embed pending WeChatAI knowledge chunks for semantic retrieval."""
    return _request(
        _query("/open/v1/knowledge/embed-now", limit=max(1, min(int(limit), 256))),
        method="POST",
        timeout=900,
    )


@server.tool()
def daily_summary_status() -> dict[str, Any]:
    """Read the current automatic daily WeChat report schedule and last delivery result."""
    return _request("/api/agent/daily-summary/status", authenticated=False)


@server.tool()
def preview_daily_summary() -> dict[str, Any]:
    """Generate a detailed report without sending it. The configured scope may cover all synchronized records."""
    return _request(
        "/api/agent/daily-summary/preview",
        method="POST",
        authenticated=False,
        timeout=900,
    )


@server.tool()
def send_daily_summary_now() -> dict[str, Any]:
    """Generate and proactively send the latest detailed report to the owner's Hermes Weixin chat."""
    return _request(
        "/api/agent/daily-summary/run",
        method="POST",
        authenticated=False,
        timeout=900,
    )


@server.tool()
def configure_daily_summary(
    enabled: bool | None = None,
    time: str | None = None,
    hours: int | None = None,
) -> dict[str, Any]:
    """Enable, disable, or reschedule the owner's daily report. hours=0 means all synchronized history."""
    payload: dict[str, Any] = {}
    if enabled is not None:
        payload["enabled"] = enabled
    if time:
        payload["time"] = time
    if hours is not None:
        payload["hours"] = max(0, min(int(hours), 720))
    return _request(
        "/api/agent/daily-summary/config",
        method="POST",
        payload=payload,
        authenticated=False,
    )


if __name__ == "__main__":
    server.run()
