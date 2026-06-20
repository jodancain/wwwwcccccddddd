"""Frontend smoke checks for a running WeChatAI Vite app.

This is intentionally lighter than a full browser test. It catches the common
blank-screen class of failures by verifying that Vite serves transformed TS/Vue
modules and that the frontend `/api` proxy can reach the backend.
"""
from __future__ import annotations

import argparse
import json
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

    status, content_type, raw = fetch(frontend_url, "/api/sync/status")
    sync = parse_json(raw)
    add(
        checks,
        "api proxy sync",
        status == 200 and isinstance(sync, dict) and sync.get("total_messages", 0) > 0,
        f"status={status} total={sync.get('total_messages') if isinstance(sync, dict) else 'n/a'}",
    )

    status, content_type, raw = fetch(frontend_url, "/api/messages/conversations")
    conversations = parse_json(raw)
    add(
        checks,
        "api proxy conversations",
        status == 200 and isinstance(conversations, list) and len(conversations) > 0,
        f"status={status} count={len(conversations) if isinstance(conversations, list) else 'n/a'}",
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
