"""Runtime smoke tests for the WeChatAI backend.

The script intentionally uses only the Python standard library so it can run
inside the existing backend venv without adding test dependencies.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


class Client:
    def __init__(self, base_url: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = None
        request_headers = {"Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, self._parse_response(resp.read(), resp.headers.get("content-type", ""))
        except urllib.error.HTTPError as exc:
            return exc.code, self._parse_response(exc.read(), exc.headers.get("content-type", ""))

    @staticmethod
    def _parse_response(raw: bytes, content_type: str) -> Any:
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8")) if raw else None
        return raw.decode("utf-8", errors="replace")


def add(checks: list[Check], name: str, ok: bool, detail: str = "") -> None:
    checks.append(Check(name=name, ok=bool(ok), detail=detail))


def query(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params)


def run(base_url: str) -> list[Check]:
    client = Client(base_url)
    checks: list[Check] = []
    created_api_id: str | None = None
    created_skill_slug: str | None = None
    talker = ""

    try:
        status, sync = client.request("GET", "/api/sync/status")
        add(
            checks,
            "sync status",
            status == 200 and isinstance(sync, dict) and sync.get("total_messages", 0) > 0,
            f"status={status} total={sync.get('total_messages') if isinstance(sync, dict) else 'n/a'}",
        )

        status, conversations = client.request("GET", "/api/messages/conversations")
        if status == 200 and isinstance(conversations, list) and conversations:
            talker = conversations[0].get("talker", "")
        add(
            checks,
            "conversation list",
            bool(talker),
            f"status={status} count={len(conversations) if isinstance(conversations, list) else 'n/a'}",
        )

        if talker:
            status, messages = client.request("GET", f"/api/messages/?{query({'talker': talker, 'page_size': 5})}")
            add(
                checks,
                "message page",
                status == 200 and isinstance(messages, dict) and "items" in messages,
                f"status={status} items={len(messages.get('items', [])) if isinstance(messages, dict) else 'n/a'}",
            )

            status, dates = client.request("GET", f"/api/messages/dates?{query({'talker': talker})}")
            add(
                checks,
                "message dates",
                status == 200 and isinstance(dates, list),
                f"status={status} count={len(dates) if isinstance(dates, list) else 'n/a'}",
            )

            status, recent = client.request("GET", f"/api/messages/recent?{query({'talker': talker, 'limit': 3})}")
            add(
                checks,
                "recent messages",
                status == 200 and isinstance(recent, list),
                f"status={status} count={len(recent) if isinstance(recent, list) else 'n/a'}",
            )

        status, contacts = client.request("GET", "/api/contacts/?limit=5")
        add(checks, "contacts list", status == 200 and isinstance(contacts, (list, dict)), f"status={status}")

        status, settings = client.request("GET", "/api/settings/")
        add(checks, "settings", status == 200 and isinstance(settings, dict), f"status={status}")

        status, wechat = client.request("GET", "/api/settings/wechat/status")
        add(checks, "wechat status", status == 200 and isinstance(wechat, dict), f"status={status}")

        if talker:
            status, replies = client.request("POST", "/api/ai/suggest-replies", {"talker": talker})
            add(
                checks,
                "ai suggest replies",
                status == 200 and isinstance(replies, dict) and len(replies.get("replies", [])) > 0,
                f"status={status} replies={len(replies.get('replies', [])) if isinstance(replies, dict) else 'n/a'}",
            )

        status, sessions = client.request("GET", "/api/ai/sessions")
        add(
            checks,
            "ai sessions",
            status == 200 and isinstance(sessions, list),
            f"status={status} count={len(sessions) if isinstance(sessions, list) else 'n/a'}",
        )

        status, stats = client.request("GET", "/api/training/stats")
        add(checks, "training stats", status == 200 and isinstance(stats, dict), f"status={status}")

        status, training = client.request("GET", "/api/training/status")
        add(
            checks,
            "training status",
            status == 200 and isinstance(training, dict) and "stage" in training,
            f"status={status} stage={training.get('stage') if isinstance(training, dict) else 'n/a'}",
        )

        status, models = client.request("GET", "/api/training/models")
        add(
            checks,
            "training models",
            status == 200 and isinstance(models, dict) and "models" in models,
            f"status={status} count={len(models.get('models', [])) if isinstance(models, dict) else 'n/a'}",
        )

        if talker:
            status, my_reply = client.request("POST", "/api/training/my-reply", {"talker": talker})
            unavailable = "\u5206\u8eab\u6a21\u578b\u672a\u8fd0\u884c"
            add(
                checks,
                "my-reply fallback/error",
                status == 200
                and isinstance(my_reply, dict)
                and ("reply" in my_reply or unavailable in my_reply.get("error", "")),
                f"status={status}",
            )

        status, skills = client.request("GET", "/api/skills/")
        add(
            checks,
            "skills list",
            status == 200 and isinstance(skills, list),
            f"status={status} count={len(skills) if isinstance(skills, list) else 'n/a'}",
        )

        created_skill_slug = "codex-smoke-skill"
        skill_content = "---\nname: codex-smoke-skill\ndescription: \"smoke\"\n---\n\n# Smoke\n"
        status, saved = client.request(
            "POST",
            "/api/skills/save",
            {"slug": created_skill_slug, "content": skill_content},
        )
        add(checks, "skill save", status == 200 and isinstance(saved, dict), f"status={status}")

        status, skill = client.request("GET", f"/api/skills/{created_skill_slug}")
        add(
            checks,
            "skill get",
            status == 200 and isinstance(skill, dict) and skill.get("content", "").startswith("---"),
            f"status={status}",
        )

        if talker:
            status, api = client.request(
                "POST",
                "/api/chat-apis/create",
                {"talker": talker, "name": "Codex Smoke API"},
            )
            api_ok = status == 200 and isinstance(api, dict) and api.get("id") and api.get("api_key")
            add(checks, "chat api create", api_ok, f"status={status}")

            if api_ok:
                created_api_id = api["id"]
                api_key = api["api_key"]
                status, _ = client.request("GET", f"/open/v1/{created_api_id}/info")
                add(checks, "open api missing auth", status == 401, f"status={status}")

                auth_query = query({"api_key": api_key})
                status, info = client.request("GET", f"/open/v1/{created_api_id}/info?{auth_query}")
                add(
                    checks,
                    "open api info",
                    status == 200 and isinstance(info, dict) and info.get("talker") == talker,
                    f"status={status}",
                )

                recent_query = query({"api_key": api_key, "limit": 2})
                status, recent = client.request("GET", f"/open/v1/{created_api_id}/messages/recent?{recent_query}")
                add(
                    checks,
                    "open api recent",
                    status == 200 and isinstance(recent, list),
                    f"status={status} count={len(recent) if isinstance(recent, list) else 'n/a'}",
                )
    finally:
        if created_api_id:
            status, _ = client.request("DELETE", f"/api/chat-apis/{created_api_id}")
            add(checks, "cleanup chat api", status == 200, f"status={status}")
        if created_skill_slug:
            status, _ = client.request("DELETE", f"/api/skills/{created_skill_slug}")
            add(checks, "cleanup skill", status == 200, f"status={status}")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WeChatAI backend smoke tests.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    args = parser.parse_args()

    checks = run(args.base_url)
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
