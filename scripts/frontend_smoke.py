"""Frontend smoke checks for a running WeChatAI Vite app.

This is intentionally lighter than a full browser test. It catches the common
blank-screen class of failures by verifying that Vite serves transformed TS/Vue
modules and that the frontend `/api` proxy can reach the backend.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def fetch(base_url: str, path: str, timeout: int = 15) -> tuple[int, str, bytes]:
    url = base_url.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.headers.get("content-type", ""), resp.read()


def add(checks: list[Check], name: str, ok: bool, detail: str = "") -> None:
    checks.append(Check(name=name, ok=bool(ok), detail=detail))


def parse_json(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def has_keys(value: Any, keys: set[str]) -> bool:
    return isinstance(value, dict) and keys.issubset(value.keys())


def websocket_url(frontend_url: str) -> str:
    parsed = urllib.parse.urlparse(frontend_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urllib.parse.urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))


def websocket_probe(ws_url: str) -> tuple[bool, str]:
    try:
        parsed = urllib.parse.urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        ).encode("ascii")

        with socket.create_connection((host, port), timeout=10) as sock:
            if parsed.scheme == "wss":
                with ssl.create_default_context().wrap_socket(sock, server_hostname=host) as tls_sock:
                    tls_sock.sendall(request)
                    response = tls_sock.recv(1024).decode("ascii", errors="replace")
            else:
                sock.sendall(request)
                response = sock.recv(1024).decode("ascii", errors="replace")

        if " 101 " in response.splitlines()[0] and "upgrade: websocket" in response.lower():
            return True, "connected"
        return False, response.splitlines()[0] if response else "empty response"
    except Exception as exc:
        return False, str(exc)


def run(frontend_url: str) -> list[Check]:
    checks: list[Check] = []

    status, content_type, raw = fetch(frontend_url, "/")
    html = raw.decode("utf-8", errors="replace")
    add(
        checks,
        "html entry",
        status == 200 and "text/html" in content_type and 'id="app"' in html and "/src/main.ts" in html,
        f"status={status} content-type={content_type}",
    )

    status, content_type, raw = fetch(frontend_url, "/src/main.ts")
    main_ts = raw.decode("utf-8", errors="replace")
    add(
        checks,
        "vite main module",
        status == 200 and "javascript" in content_type and "createApp" in main_ts and "/src/App.vue" in main_ts,
        f"status={status} content-type={content_type}",
    )

    status, content_type, raw = fetch(frontend_url, "/src/App.vue")
    app_vue = raw.decode("utf-8", errors="replace")
    add(
        checks,
        "vite vue transform",
        status == 200
        and "javascript" in content_type
        and "import.meta.hot" in app_vue
        and "ConversationList" in app_vue,
        f"status={status} content-type={content_type}",
    )

    component_expectations = [
        (
            "/src/components/ConversationList.vue",
            "conversation component",
            ["conversation-list", "conversation-item", "loadError", "result?.value"],
        ),
        ("/src/components/MessageThread.vue", "message component", ["message-thread", "date-jump-btn"]),
        ("/src/components/AIChatPanel.vue", "ai panel component", ["ai-panel", "quick-action-btn"]),
        (
            "/src/api/index.ts",
            "api module",
            ["getConversations", "readSseJson", "aiChatStream", "globalSummaryStream", "generateSkillStream"],
        ),
        (
            "/src/composables/useAIChat.ts",
            "ai chat composable",
            ["lastError", "加载 AI 会话失败", "获取快捷回复失败", "streamingContent.value ||"],
        ),
    ]
    for path, name, needles in component_expectations:
        status, content_type, raw = fetch(frontend_url, path)
        body = raw.decode("utf-8", errors="replace")
        add(
            checks,
            name,
            status == 200
            and "javascript" in content_type
            and all(needle in body for needle in needles)
            and "\ufffd" not in body,
            f"status={status} content-type={content_type}",
        )

    status, content_type, raw = fetch(frontend_url, "/api/sync/status")
    sync = parse_json(raw)
    add(
        checks,
        "api proxy sync",
        status == 200 and isinstance(sync, dict) and sync.get("total_messages", 0) > 0,
        f"status={status} total={sync.get('total_messages') if isinstance(sync, dict) else 'n/a'}",
    )

    ws_ok, ws_detail = websocket_probe(websocket_url(frontend_url))
    add(checks, "websocket proxy", ws_ok, ws_detail)

    status, content_type, raw = fetch(frontend_url, "/api/messages/conversations")
    conversations = parse_json(raw)
    add(
        checks,
        "api proxy conversations",
        status == 200 and isinstance(conversations, list) and len(conversations) > 0,
        f"status={status} count={len(conversations) if isinstance(conversations, list) else 'n/a'}",
    )
    add(
        checks,
        "api proxy conversation shape",
        status == 200
        and isinstance(conversations, list)
        and bool(conversations)
        and has_keys(conversations[0], {"talker", "nickname", "remark", "is_group", "msg_count", "last_time"}),
        f"status={status}",
    )

    if isinstance(conversations, list) and conversations:
        talker = conversations[0].get("talker", "")
        params = urllib.parse.urlencode({"talker": talker, "page_size": 5})
        status, content_type, raw = fetch(frontend_url, f"/api/messages/?{params}")
        messages = parse_json(raw)
        add(
            checks,
            "api proxy messages",
            status == 200 and isinstance(messages, dict) and isinstance(messages.get("items"), list),
            f"status={status} items={len(messages.get('items', [])) if isinstance(messages, dict) else 'n/a'}",
        )
        first_message = messages.get("items", [{}])[0] if isinstance(messages, dict) and messages.get("items") else {}
        add(
            checks,
            "api proxy message shape",
            status == 200
            and isinstance(messages, dict)
            and has_keys(messages, {"items", "total", "page", "page_size"})
            and has_keys(first_message, {"id", "talker", "content", "create_time", "type", "is_sender"}),
            f"status={status}",
        )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WeChatAI frontend smoke checks.")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5175")
    args = parser.parse_args()

    checks = run(args.frontend_url)
    for check in checks:
        status = "PASS" if check.ok else "FAIL"
        print(f"{status:4} {check.name:24} {check.detail}")

    failed = [check for check in checks if not check.ok]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if failed:
        print("Failed checks:")
        for check in failed:
            print(f"- {check.name}: {check.detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
