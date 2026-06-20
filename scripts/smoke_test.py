"""Runtime smoke tests for the WeChatAI backend.

The script intentionally uses only the Python standard library so it can run
inside the existing backend venv without adding test dependencies.
"""
from __future__ import annotations

import argparse
import base64
import os
import json
import socket
import sys
import ssl
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

    def stream_events(self, path: str, body: dict[str, Any], timeout: int = 90) -> tuple[int, list[dict[str, Any]]]:
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        events: list[dict[str, Any]] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            status = exc.code

        for line in text.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                events.append({"raw": line[6:]})
        return status, events

    @staticmethod
    def _parse_response(raw: bytes, content_type: str) -> Any:
        if "application/json" in content_type:
            return json.loads(raw.decode("utf-8")) if raw else None
        return raw.decode("utf-8", errors="replace")


def add(checks: list[Check], name: str, ok: bool, detail: str = "") -> None:
    checks.append(Check(name=name, ok=bool(ok), detail=detail))


def query(params: dict[str, Any]) -> str:
    return urllib.parse.urlencode(params)


def _masked_secret(value: Any) -> bool:
    return value == "" or (isinstance(value, str) and value.startswith("***") and len(value) == 7)


def _websocket_probe(ws_url: str) -> tuple[bool, str]:
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


def websocket_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urllib.parse.urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))


def run(base_url: str, include_heavy: bool = False) -> list[Check]:
    client = Client(base_url)
    checks: list[Check] = []
    created_api_id: str | None = None
    created_skill_slug: str | None = None
    generated_skill_slug: str | None = None
    ai_session_ids: set[str] = set()
    talker = ""

    try:
        status, sync = client.request("GET", "/api/sync/status")
        add(
            checks,
            "sync status",
            status == 200 and isinstance(sync, dict) and sync.get("total_messages", 0) > 0,
            f"status={status} total={sync.get('total_messages') if isinstance(sync, dict) else 'n/a'}",
        )

        status, realtime = client.request("GET", "/api/sync/realtime-status")
        add(
            checks,
            "realtime status",
            status == 200 and isinstance(realtime, dict) and "running" in realtime,
            f"status={status} running={realtime.get('running') if isinstance(realtime, dict) else 'n/a'}",
        )

        ws_ok, ws_detail = _websocket_probe(websocket_url(base_url))
        add(checks, "websocket connect", ws_ok, ws_detail)

        if include_heavy:
            status, triggered = client.request("POST", "/api/sync/trigger")
            add(
                checks,
                "sync trigger",
                status == 200
                and isinstance(triggered, dict)
                and triggered.get("message") == "Sync triggered",
                f"status={status}",
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

            if status == 200 and isinstance(dates, list) and dates:
                first_date = dates[0].get("date", "")
                status, by_date = client.request(
                    "GET",
                    f"/api/messages/by-date?{query({'talker': talker, 'date': first_date, 'page_size': 5})}",
                )
                add(
                    checks,
                    "messages by date",
                    status == 200 and isinstance(by_date, dict) and isinstance(by_date.get("items"), list),
                    f"status={status} items={len(by_date.get('items', [])) if isinstance(by_date, dict) else 'n/a'}",
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

        status, friend_contacts = client.request("GET", "/api/contacts/?type=friend&limit=3")
        add(
            checks,
            "friend contacts filter",
            status == 200 and isinstance(friend_contacts, list),
            f"status={status} count={len(friend_contacts) if isinstance(friend_contacts, list) else 'n/a'}",
        )

        status, group_contacts = client.request("GET", "/api/contacts/?type=group&limit=3")
        add(
            checks,
            "group contacts filter",
            status == 200 and isinstance(group_contacts, list),
            f"status={status} count={len(group_contacts) if isinstance(group_contacts, list) else 'n/a'}",
        )

        status, media = client.request("GET", "/api/media/image/999999999")
        add(checks, "media missing image", status == 404, f"status={status}")

        status, send_validation = client.request("POST", "/api/send/text", {"contact_name": ""})
        add(checks, "send missing content validation", status == 422, f"status={status}")

        status, send_validation = client.request("POST", "/api/send/text", {"contact_name": "  ", "content": "hello"})
        add(checks, "send blank contact validation", status == 422, f"status={status}")

        status, send_validation = client.request("POST", "/api/send/text", {"contact_name": "Codex", "content": "  "})
        add(checks, "send blank content validation", status == 422, f"status={status}")

        status, settings = client.request("GET", "/api/settings/")
        add(checks, "settings", status == 200 and isinstance(settings, dict), f"status={status}")
        add(
            checks,
            "settings masks API keys",
            status == 200
            and isinstance(settings, dict)
            and _masked_secret(settings.get("GEMINI_API_KEY", ""))
            and _masked_secret(settings.get("OPENAI_API_KEY", "")),
            f"status={status}",
        )

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

            status, chat = client.request(
                "POST",
                "/api/ai/chat",
                {"talker": talker, "message": "Codex smoke test: summarize the latest context briefly."},
            )
            if status == 200 and isinstance(chat, dict) and chat.get("session_id"):
                ai_session_ids.add(chat["session_id"])
            add(
                checks,
                "ai chat fallback",
                status == 200
                and isinstance(chat, dict)
                and bool(chat.get("session_id"))
                and bool(chat.get("response")),
                f"status={status}",
            )

            if status == 200 and isinstance(chat, dict) and chat.get("session_id"):
                session_id = chat["session_id"]
                status, session_messages = client.request("GET", f"/api/ai/sessions/{session_id}/messages")
                add(
                    checks,
                    "ai session messages",
                    status == 200 and isinstance(session_messages, list) and len(session_messages) >= 2,
                    f"status={status} count={len(session_messages) if isinstance(session_messages, list) else 'n/a'}",
                )

            status, chat_events = client.stream_events(
                "/api/ai/chat/stream",
                {"talker": talker, "message": "Codex smoke stream test."},
            )
            stream_done = [event for event in chat_events if event.get("done")]
            stream_session_id = stream_done[-1].get("session_id") if stream_done else ""
            if stream_session_id:
                ai_session_ids.add(stream_session_id)
            add(
                checks,
                "ai chat stream fallback",
                status == 200 and any("chunk" in event for event in chat_events) and bool(stream_session_id),
                f"status={status} events={len(chat_events)}",
            )

            status, summary_events = client.stream_events(
                "/api/ai/global-summary/stream",
                {"hours": 1, "message": "Codex smoke test: summarize recent chats briefly."},
            )
            summary_done = [event for event in summary_events if event.get("done")]
            summary_session_id = ""
            for event in reversed(summary_events):
                if event.get("session_id"):
                    summary_session_id = event["session_id"]
                    break
            if summary_session_id:
                ai_session_ids.add(summary_session_id)
            add(
                checks,
                "global summary stream",
                status == 200
                and any("chunk" in event for event in summary_events)
                and bool(summary_done),
                f"status={status} events={len(summary_events)}",
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

        if include_heavy:
            status, export = client.request("POST", "/api/training/export-data")
            add(
                checks,
                "training export data",
                status == 200
                and isinstance(export, dict)
                and "output_file" in export
                and "dataset_info_file" in export,
                f"status={status} conversations={export.get('total_conversations') if isinstance(export, dict) else 'n/a'}",
            )

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

        status, scanned_models = client.request("POST", "/api/training/models/scan", {"roots": []})
        add(
            checks,
            "training model scan",
            status == 200 and isinstance(scanned_models, dict) and "found" in scanned_models,
            f"status={status} found={len(scanned_models.get('found', [])) if isinstance(scanned_models, dict) else 'n/a'}",
        )

        status, invalid_model_import = client.request(
            "POST",
            "/api/training/models/import",
            {"path": "Z:/codex-smoke/missing-model", "model_type": "full"},
        )
        add(
            checks,
            "training model invalid import",
            status == 400
            and isinstance(invalid_model_import, dict)
            and "detail" in invalid_model_import,
            f"status={status}",
        )

        missing_model_id = "codex-smoke-missing-model"
        status, missing_activate = client.request("POST", f"/api/training/models/{missing_model_id}/activate")
        add(
            checks,
            "training model missing activate",
            status == 404
            and isinstance(missing_activate, dict)
            and "detail" in missing_activate,
            f"status={status}",
        )

        status, missing_delete = client.request("DELETE", f"/api/training/models/{missing_model_id}")
        add(
            checks,
            "training model missing delete",
            status == 404
            and isinstance(missing_delete, dict)
            and "detail" in missing_delete,
            f"status={status}",
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

            status, skill_events = client.stream_events("/api/skills/generate/stream", {"talker": talker})
            done_events = [event for event in skill_events if event.get("done")]
            generated_skill_slug = done_events[-1].get("slug") if done_events else None
            add(
                checks,
                "skill generate stream",
                status == 200
                and any("chunk" in event for event in skill_events)
                and bool(generated_skill_slug),
                f"status={status} events={len(skill_events)} slug={generated_skill_slug or 'n/a'}",
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

                status, api_list = client.request("GET", "/api/chat-apis/")
                listed_api = None
                if isinstance(api_list, list):
                    listed_api = next((item for item in api_list if item.get("id") == created_api_id), None)
                add(
                    checks,
                    "chat api list masks key",
                    status == 200
                    and isinstance(listed_api, dict)
                    and "api_key" not in listed_api
                    and isinstance(listed_api.get("api_key_preview"), str)
                    and listed_api["api_key_preview"].endswith(api_key[-8:]),
                    f"status={status}",
                )

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

                status, messages = client.request(
                    "GET",
                    f"/open/v1/{created_api_id}/messages?{query({'api_key': api_key, 'page_size': 2})}",
                )
                add(
                    checks,
                    "open api messages",
                    status == 200 and isinstance(messages, dict) and isinstance(messages.get("items"), list),
                    f"status={status} items={len(messages.get('items', [])) if isinstance(messages, dict) else 'n/a'}",
                )

                recent_query = query({"api_key": api_key, "limit": 2})
                status, recent = client.request("GET", f"/open/v1/{created_api_id}/messages/recent?{recent_query}")
                add(
                    checks,
                    "open api recent",
                    status == 200 and isinstance(recent, list),
                    f"status={status} count={len(recent) if isinstance(recent, list) else 'n/a'}",
                )

                status, search = client.request(
                    "GET",
                    f"/open/v1/{created_api_id}/search?{query({'api_key': api_key, 'q': 'a', 'page_size': 2})}",
                )
                add(
                    checks,
                    "open api search",
                    status == 200 and isinstance(search, dict) and "items" in search,
                    f"status={status} items={len(search.get('items', [])) if isinstance(search, dict) else 'n/a'}",
                )

                status, toggled = client.request("POST", f"/api/chat-apis/{created_api_id}/toggle")
                add(
                    checks,
                    "chat api toggle off",
                    status == 200 and isinstance(toggled, dict) and toggled.get("enabled") in (0, False),
                    f"status={status}",
                )

                status, unauthorized = client.request("GET", f"/open/v1/{created_api_id}/info?{auth_query}")
                add(checks, "open api disabled auth", status == 401, f"status={status}")

                status, toggled = client.request("POST", f"/api/chat-apis/{created_api_id}/toggle")
                add(
                    checks,
                    "chat api toggle on",
                    status == 200 and isinstance(toggled, dict) and toggled.get("enabled") in (1, True),
                    f"status={status}",
                )
    finally:
        if created_api_id:
            status, _ = client.request("DELETE", f"/api/chat-apis/{created_api_id}")
            add(checks, "cleanup chat api", status == 200, f"status={status}")
        if generated_skill_slug:
            status, _ = client.request("DELETE", f"/api/skills/{generated_skill_slug}")
            add(checks, "cleanup generated skill", status == 200, f"status={status}")
        if created_skill_slug:
            status, _ = client.request("DELETE", f"/api/skills/{created_skill_slug}")
            add(checks, "cleanup skill", status == 200, f"status={status}")
        for session_id in sorted(ai_session_ids):
            status, _ = client.request("DELETE", f"/api/ai/sessions/{session_id}")
            add(checks, f"cleanup ai session {session_id}", status == 200, f"status={status}")

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WeChatAI backend smoke tests.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument(
        "--include-heavy",
        action="store_true",
        help="Also run heavier checks that write training exports or trigger sync.",
    )
    args = parser.parse_args()

    checks = run(args.base_url, include_heavy=args.include_heavy)
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
