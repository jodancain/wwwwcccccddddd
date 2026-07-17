from __future__ import annotations

import asyncio
import html
import json
import os
import secrets
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from loguru import logger

from app.ai.context_builder import GLOBAL_SUMMARY_SYSTEM_PROMPT, build_global_context
from app.ai.fallback import fallback_global_summary
from app.ai.gemini_provider import GeminiProvider
from app.ai.openai_provider import OpenAIProvider
from app.ai.provider_base import AIProvider
from app.config.settings import get_settings
from app.dependencies import get_db
from app.knowledge.source_extractor import source_extractor
from app.wechat_sender.automator import WeChatAutomator


@dataclass
class DailySummaryConfig:
    enabled: bool
    receiver: str
    time: str
    hours: int
    max_messages: int


@dataclass
class DailySummaryStatus:
    enabled: bool
    receiver: str
    time: str
    hours: int
    max_messages: int
    running: bool
    last_run_at: str = ""
    last_status: str = ""
    last_error: str = ""
    next_run_at: str = ""

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "receiver": self.receiver,
            "time": self.time,
            "hours": self.hours,
            "max_messages": self.max_messages,
            "running": self.running,
            "last_run_at": self.last_run_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
        }


class DailySummaryScheduler:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.automator = WeChatAutomator()
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._running = False
        self._last_run_at = ""
        self._last_status = ""
        self._last_error = ""
        self._next_run_at = ""
        self._send_lock = asyncio.Lock()

    async def status(self) -> dict:
        config = await self.get_config()
        self._refresh_next_run_at(config)
        db = await get_db()
        last_run_at, last_status, last_error = await self._load_last_run_snapshot(db)
        return DailySummaryStatus(
            enabled=config.enabled,
            receiver=config.receiver,
            time=config.time,
            hours=config.hours,
            max_messages=config.max_messages,
            running=self._running,
            last_run_at=last_run_at,
            last_status=last_status,
            last_error=last_error,
            next_run_at=self._next_run_at,
        ).to_dict()

    async def start(self) -> None:
        self._running = True
        logger.info("Daily summary scheduler started")
        try:
            while not self._stop_event.is_set():
                config = await self.get_config()
                if not config.enabled:
                    self._refresh_next_run_at(config)
                    wait_result = await self._wait_for_change_or_stop(60)
                    if wait_result == "stop":
                        break
                    continue

                next_run = self._next_run_time(datetime.now(), config.time)
                self._next_run_at = next_run.isoformat(timespec="seconds")
                wait_seconds = max(1.0, (next_run - datetime.now()).total_seconds())
                wait_result = await self._wait_for_change_or_stop(wait_seconds)
                if wait_result == "stop":
                    break
                if wait_result == "timeout":
                    await self.run_once(reason="schedule")
        finally:
            self._running = False
            logger.info("Daily summary scheduler stopped")

    def stop(self) -> None:
        self._stop_event.set()

    async def get_config(self) -> DailySummaryConfig:
        db = await get_db()
        enabled_raw = await db.get_setting(
            "daily_summary.enabled",
            "true" if self.settings.DAILY_SUMMARY_ENABLED else "false",
        )
        receiver = await db.get_setting("daily_summary.receiver", self.settings.DAILY_SUMMARY_RECEIVER)
        time_value = await db.get_setting("daily_summary.time", self.settings.DAILY_SUMMARY_TIME)
        hours_raw = await db.get_setting("daily_summary.hours", str(self.settings.DAILY_SUMMARY_HOURS))
        max_messages_raw = await db.get_setting(
            "daily_summary.max_messages",
            str(self.settings.DAILY_SUMMARY_MAX_MESSAGES),
        )
        return DailySummaryConfig(
            enabled=str(enabled_raw).strip().lower() in {"1", "true", "yes", "on", "开启", "开"},
            receiver=(receiver or self.settings.DAILY_SUMMARY_RECEIVER).strip() or "文件传输助手",
            time=self._normalize_time(time_value),
            hours=self._parse_int(hours_raw, self.settings.DAILY_SUMMARY_HOURS, minimum=1, maximum=720),
            max_messages=self._parse_int(
                max_messages_raw,
                self.settings.DAILY_SUMMARY_MAX_MESSAGES,
                minimum=1,
                maximum=200000,
            ),
        )

    async def configure(
        self,
        *,
        enabled: bool | None = None,
        receiver: str | None = None,
        time_value: str | None = None,
        hours: int | None = None,
    ) -> dict:
        db = await get_db()
        if enabled is not None:
            await db.set_setting("daily_summary.enabled", "true" if enabled else "false", "Daily summary enabled")
        if receiver is not None and receiver.strip():
            await db.set_setting("daily_summary.receiver", receiver.strip(), "Daily summary receiver")
        if time_value is not None and time_value.strip():
            await db.set_setting("daily_summary.time", self._normalize_time(time_value), "Daily summary time")
        if hours is not None:
            safe_hours = self._parse_int(str(hours), 24, minimum=1, maximum=720)
            await db.set_setting("daily_summary.hours", str(safe_hours), "Daily summary hours")
        config = await self.get_config()
        self._refresh_next_run_at(config)
        self._wake_event.set()
        return await self.status()

    async def run_once(self, reason: str = "manual") -> dict:
        async with self._send_lock:
            now = datetime.now().isoformat(timespec="seconds")
            self._last_run_at = now
            self._last_error = ""
            db = await get_db()
            await db.set_setting("daily_summary.last_run_at", now, "Daily summary last run time")
            await db.set_setting("daily_summary.last_error", "", "Daily summary last error")
            try:
                config = await self.get_config()
                text, html_path, share_url = await self._generate_summary_artifacts(db, config, now)
                send_result = await asyncio.to_thread(self._send_summary, config.receiver, text, html_path, share_url)
                sent = bool(send_result.get("sent"))
                self._last_status = "sent" if sent else "send_failed"
                result = {
                    "status": self._last_status,
                    "reason": reason,
                    "receiver": config.receiver,
                    "run_at": now,
                    "length": len(text),
                    "sent": sent,
                    "send_method": send_result.get("method", ""),
                    "message_id": send_result.get("message_id", ""),
                    "message_ids": send_result.get("message_ids", []),
                    "send_parts": send_result.get("parts", 1),
                    "send_error": send_result.get("error", ""),
                    "html_path": str(html_path),
                    "html_sent": bool(send_result.get("html_sent")),
                    "share_url": share_url,
                    "share_sent": bool(send_result.get("share_sent")),
                }
                await db.set_setting("daily_summary.last_status", self._last_status, "Daily summary last status")
                await db.add_agent_audit("daily_summary_run", result)
                return result
            except Exception as exc:  # noqa: BLE001
                self._last_status = "failed"
                self._last_error = str(exc)
                result = {"status": "failed", "reason": reason, "run_at": now, "error": str(exc)}
                await db.set_setting("daily_summary.last_status", self._last_status, "Daily summary last status")
                await db.set_setting("daily_summary.last_error", self._last_error, "Daily summary last error")
                await db.add_agent_audit("daily_summary_failed", result)
                logger.exception(f"Daily summary failed: {exc}")
                return result

    async def create_share_report(self, reason: str = "manual_reply") -> dict:
        async with self._send_lock:
            now = datetime.now().isoformat(timespec="seconds")
            self._last_run_at = now
            self._last_error = ""
            db = await get_db()
            await db.set_setting("daily_summary.last_run_at", now, "Daily summary last run time")
            await db.set_setting("daily_summary.last_error", "", "Daily summary last error")
            try:
                config = await self.get_config()
                text, html_path, share_url = await self._generate_summary_artifacts(db, config, now)
                self._last_status = "ready"
                result = {
                    "status": self._last_status,
                    "reason": reason,
                    "receiver": config.receiver,
                    "run_at": now,
                    "length": len(text),
                    "sent": False,
                    "send_method": "wechat-reply-share-link",
                    "message_id": "",
                    "message_ids": [],
                    "send_parts": 1,
                    "send_error": "",
                    "html_path": str(html_path),
                    "html_sent": False,
                    "share_url": share_url,
                    "share_sent": False,
                    "share_message": self._build_share_message(text, share_url),
                }
                await db.set_setting("daily_summary.last_status", self._last_status, "Daily summary last status")
                await db.add_agent_audit("daily_summary_share_ready", result)
                return result
            except Exception as exc:  # noqa: BLE001
                self._last_status = "failed"
                self._last_error = str(exc)
                result = {"status": "failed", "reason": reason, "run_at": now, "error": str(exc)}
                await db.set_setting("daily_summary.last_status", self._last_status, "Daily summary last status")
                await db.set_setting("daily_summary.last_error", self._last_error, "Daily summary last error")
                await db.add_agent_audit("daily_summary_failed", result)
                logger.exception(f"Daily summary share report failed: {exc}")
                return result

    async def _generate_summary_artifacts(
        self,
        db,
        config: DailySummaryConfig,
        run_at: str,
    ) -> tuple[str, Path, str]:
        text = await self.generate_summary(config=config)
        html_path = self._write_summary_html(text, config, run_at)
        share_token = await self._get_or_create_share_token(db)
        share_url = self._build_share_url(share_token)
        await db.set_setting("daily_summary.latest_html_path", str(html_path), "Daily summary latest HTML path")
        await db.set_setting("daily_summary.latest_share_url", share_url, "Daily summary latest share URL")
        return text, html_path, share_url

    async def _load_last_run_snapshot(self, db) -> tuple[str, str, str]:
        last_run_at = self._last_run_at or await db.get_setting("daily_summary.last_run_at", "")
        last_status = self._last_status or await db.get_setting("daily_summary.last_status", "")
        last_error = self._last_error or await db.get_setting("daily_summary.last_error", "")
        if last_run_at and last_status:
            return last_run_at, last_status, last_error

        latest = await db.get_latest_agent_audit(["daily_summary_run", "daily_summary_failed"])
        if not latest:
            return last_run_at, last_status, last_error

        payload = latest.get("payload") or {}
        audit_run_at = str(payload.get("run_at") or latest.get("created_at") or "").replace(" ", "T")
        audit_status = str(payload.get("status") or ("failed" if latest.get("event_type") == "daily_summary_failed" else ""))
        audit_error = str(payload.get("error") or payload.get("send_error") or "")
        if audit_run_at and not last_run_at:
            last_run_at = audit_run_at
            await db.set_setting("daily_summary.last_run_at", last_run_at, "Daily summary last run time")
        if audit_status and not last_status:
            last_status = audit_status
            await db.set_setting("daily_summary.last_status", last_status, "Daily summary last status")
        if audit_error and not last_error:
            last_error = audit_error
            await db.set_setting("daily_summary.last_error", last_error, "Daily summary last error")
        return last_run_at, last_status, last_error

    async def _get_or_create_share_token(self, db) -> str:
        token = await db.get_setting("daily_summary.share_token", "")
        if token.strip():
            return token.strip()
        token = secrets.token_urlsafe(24)
        await db.set_setting("daily_summary.share_token", token, "Daily summary share token")
        return token

    async def verify_share_token(self, token: str) -> bool:
        if not token:
            return False
        db = await get_db()
        expected = await db.get_setting("daily_summary.share_token", "")
        return bool(expected) and secrets.compare_digest(str(token), str(expected))

    def latest_summary_html_path(self) -> Path:
        return (self.settings.data_path / "daily_summaries" / "latest.html").resolve()

    def _send_summary(
        self,
        receiver: str,
        text: str,
        html_path: Path | None = None,
        share_url: str = "",
    ) -> dict:
        if self._is_weixin_bot_receiver(receiver):
            return self._send_via_openclaw_weixin(text, html_path, share_url)

        sent = self.automator.send_text(receiver, text)
        return {"sent": sent, "method": "wechat_automator"}

    def _is_weixin_bot_receiver(self, receiver: str) -> bool:
        normalized = (receiver or "").strip().lower()
        return normalized in {
            "weixinclawbot",
            (self.settings.AGENT_WECHAT_ENTRY_NAME or "").strip().lower(),
            "bot",
        }

    def _write_summary_html(self, text: str, config: DailySummaryConfig, run_at: str) -> Path:
        out_dir = (self.settings.data_path / "daily_summaries").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = "".join(ch for ch in run_at if ch.isdigit())[:14] or datetime.now().strftime("%Y%m%d%H%M%S")
        out_path = out_dir / f"wechat_daily_summary_{stamp}.html"
        rendered = self._render_summary_html(text, config, run_at)
        out_path.write_text(rendered, encoding="utf-8")
        (out_dir / "latest.html").write_text(rendered, encoding="utf-8")
        return out_path

    def _build_share_url(self, token: str) -> str:
        base = (self.settings.DAILY_SUMMARY_SHARE_BASE_URL or "").strip().rstrip("/")
        if not base:
            base = self._auto_share_base_url()
        return f"{base}/share/daily/latest?token={quote(token)}"

    def _auto_share_base_url(self) -> str:
        try:
            completed = subprocess.run(
                ["tailscale", "ip", "-4"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            ip = (completed.stdout or "").strip().splitlines()[0].strip()
            if completed.returncode == 0 and ip:
                return f"http://{ip}:{self.settings.APP_PORT}"
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Unable to resolve Tailscale IP for daily summary share URL: {exc}")
        host = self.settings.APP_HOST if self.settings.APP_HOST not in {"0.0.0.0", "::"} else "127.0.0.1"
        return f"http://{host}:{self.settings.APP_PORT}"

    def _build_share_message(self, text: str, share_url: str) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
        title = lines[0].strip("# ").strip() if lines else "每日微信总结"
        preview: list[str] = []
        for line in lines[1:]:
            clean = line.strip().lstrip("-*•0123456789.、) ").strip()
            if not clean or clean.startswith("#"):
                continue
            if len(clean) > 90:
                clean = clean[:90].rstrip() + "..."
            preview.append(clean)
            if len(preview) >= 5:
                break
        preview_text = "\n".join(f"- {item}" for item in preview)
        if not preview_text:
            preview_text = "- 完整内容已生成，点下面链接查看。"
        return (
            "每日微信总结已生成\n\n"
            f"{title}\n\n"
            "重点预览：\n"
            f"{preview_text}\n\n"
            "打开完整报告：\n"
            f"{share_url}"
        )

    def _render_summary_html(self, text: str, config: DailySummaryConfig, run_at: str) -> str:
        title = next((line.strip("# ").strip() for line in (text or "").splitlines() if line.strip()), "WeChat Daily Summary")
        escaped_title = html.escape(title)
        escaped_text = html.escape(text or "")
        generated_at = html.escape(run_at.replace("T", " "))
        hours = html.escape(str(config.hours))
        max_messages = html.escape(str(config.max_messages))
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #15171a;
      --muted: #69707a;
      --border: #dfe3e8;
      --accent: #0f8f62;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #111315;
        --panel: #1b1f23;
        --text: #eef1f4;
        --muted: #a5adb7;
        --border: #30363d;
        --accent: #41d695;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.72;
    }}
    main {{
      width: min(980px, calc(100% - 28px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      margin-bottom: 18px;
      padding-bottom: 16px;
    }}
    h1 {{
      font-size: clamp(24px, 5vw, 38px);
      line-height: 1.25;
      margin: 0 0 10px;
      letter-spacing: 0;
    }}
    .meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    .chip {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 3px 10px;
      background: color-mix(in srgb, var(--panel) 80%, transparent);
    }}
    article {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: clamp(16px, 4vw, 30px);
      box-shadow: 0 8px 28px rgba(0, 0, 0, 0.06);
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
      font: inherit;
    }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escaped_title}</h1>
      <div class="meta">
        <span class="chip">generated {generated_at}</span>
        <span class="chip">range {hours}h</span>
        <span class="chip">max messages {max_messages}</span>
      </div>
    </header>
    <article>
      <pre>{escaped_text}</pre>
    </article>
  </main>
</body>
</html>
"""

    def _send_via_openclaw_weixin(
        self,
        text: str,
        html_path: Path | None = None,
        share_url: str = "",
    ) -> dict:
        try:
            account_id, target = self._resolve_openclaw_weixin_target()
            if share_url:
                share_text = self._build_share_message(text, share_url)
                share_result = self._send_openclaw_weixin_message(account_id, target, share_text)
                if share_result.get("sent"):
                    return {
                        **share_result,
                        "method": "openclaw-weixin-share-link",
                        "share_url": share_url,
                        "share_sent": True,
                        "parts": 1,
                    }
                logger.warning(
                    "OpenClaw share-link daily summary send failed, falling back to HTML file: "
                    f"{share_result.get('error', '')}"
                )
            if html_path and html_path.exists():
                file_result = self._send_openclaw_weixin_file(account_id, target, html_path)
                if file_result.get("sent"):
                    return {
                        **file_result,
                        "method": "openclaw-weixin-html-file",
                        "html_path": str(html_path),
                        "html_sent": True,
                        "parts": 1,
                    }
                logger.warning(
                    "Strict OpenClaw HTML daily summary send failed, falling back to strict text send: "
                    f"{file_result.get('error', '')}"
                )
                gateway_file_result = self._send_openclaw_weixin_gateway_file(account_id, target, html_path)
                if gateway_file_result.get("sent"):
                    return {
                        **gateway_file_result,
                        "method": "openclaw-weixin-html-file",
                        "html_path": str(html_path),
                        "html_sent": True,
                        "parts": 1,
                    }
                logger.warning(
                    "OpenClaw gateway HTML daily summary fallback failed, falling back to strict text send: "
                    f"{gateway_file_result.get('error', '')}"
                )
            parts = self._split_weixin_text(text)
            message_ids = []
            for index, part in enumerate(parts, start=1):
                body = part
                if len(parts) > 1:
                    body = f"每日微信总结 ({index}/{len(parts)})\n\n{part}"
                result = self._send_openclaw_weixin_message(account_id, target, body)
                if not result.get("sent"):
                    return {
                        "sent": False,
                        "method": "openclaw-weixin",
                        "message_id": message_ids[-1] if message_ids else "",
                        "message_ids": message_ids,
                        "error": result.get("error", ""),
                    }
                message_ids.append(result.get("message_id", ""))
                if index < len(parts):
                    time.sleep(0.8)
            return {
                "sent": bool(message_ids),
                "method": "openclaw-weixin",
                "message_id": message_ids[-1] if message_ids else "",
                "message_ids": message_ids,
                "parts": len(parts),
            }
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "method": "openclaw-weixin", "error": str(exc)}

    def _send_openclaw_weixin_gateway_file(self, account_id: str, target: str, file_path: Path) -> dict:
        try:
            file_path = file_path.resolve()
            cli_path = Path.home() / "AppData" / "Roaming" / "npm" / "openclaw.cmd"
            completed = subprocess.run(
                [
                    str(cli_path) if cli_path.exists() else "openclaw.cmd",
                    "message",
                    "send",
                    "--channel",
                    "openclaw-weixin",
                    "--account",
                    account_id,
                    "--target",
                    target,
                    "--media",
                    str(file_path),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=240,
                cwd=str(Path.home()),
            )
            combined = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            payload = self._parse_first_json_object(combined)
            message_id = str(payload.get("messageId") or (payload.get("payload") or {}).get("messageId") or "")
            if not message_id:
                result = (payload.get("payload") or {}).get("result") or {}
                message_id = str(result.get("messageId") or "")
            if message_id:
                warning = combined[-1600:] if completed.returncode != 0 else ""
                return {
                    "sent": True,
                    "method": "openclaw-weixin-gateway-file",
                    "message_id": message_id,
                    "file_name": file_path.name,
                    "file_size": file_path.stat().st_size if file_path.exists() else "",
                    "error": "",
                    "warning": warning,
                }
            if completed.returncode != 0:
                return {
                    "sent": False,
                    "method": "openclaw-weixin-gateway-file",
                    "error": combined[-1600:],
                }
            return {
                "sent": False,
                "method": "openclaw-weixin-gateway-file",
                "message_id": message_id,
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size if file_path.exists() else "",
                "error": combined[-1600:],
            }
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "method": "openclaw-weixin-gateway-file", "error": str(exc)}

    def _send_openclaw_weixin_file(self, account_id: str, target: str, file_path: Path) -> dict:
        try:
            file_path = file_path.resolve()
            plugin_root = (
                Path.home()
                / ".openclaw"
                / "npm"
                / "projects"
                / "tencent-weixin-openclaw-weixin-7783ac86ba"
                / "node_modules"
                / "@tencent-weixin"
                / "openclaw-weixin"
                / "dist"
                / "src"
            )
            plugin_api = plugin_root / "api" / "api.js"
            plugin_upload = plugin_root / "cdn" / "upload.js"
            plugin_accounts = plugin_root / "auth" / "accounts.js"
            for required in (plugin_api, plugin_upload, plugin_accounts):
                if not required.exists():
                    raise RuntimeError(f"OpenClaw Weixin plugin file not found: {required}")

            api_url = plugin_api.as_posix()
            upload_url = plugin_upload.as_posix()
            accounts_url = plugin_accounts.as_posix()
            script = f"""
import fs from 'node:fs';
import path from 'node:path';
import {{ apiPostFetch, buildBaseInfo }} from 'file:///{api_url}';
import {{ uploadFileAttachmentToWeixin }} from 'file:///{upload_url}';
import {{ CDN_BASE_URL }} from 'file:///{accounts_url}';

const accountId = process.env.OPENCLAW_WEIXIN_ACCOUNT_ID;
const target = process.env.OPENCLAW_WEIXIN_TARGET;
const filePath = process.env.OPENCLAW_WEIXIN_FILE_PATH;
const accountPath = `${{process.env.USERPROFILE}}/.openclaw/openclaw-weixin/accounts/${{accountId}}.json`;
const tokenPath = `${{process.env.USERPROFILE}}/.openclaw/openclaw-weixin/accounts/${{accountId}}.context-tokens.json`;
const account = JSON.parse(fs.readFileSync(accountPath, 'utf8'));
const tokens = fs.existsSync(tokenPath) ? JSON.parse(fs.readFileSync(tokenPath, 'utf8')) : {{}};
const omitContext = process.env.OPENCLAW_WEIXIN_OMIT_CONTEXT === '1';
const storedContextToken = tokens[target] || '';
const contextToken = omitContext ? '' : storedContextToken;
const fileName = path.basename(filePath);
const timeoutMs = 60000;

try {{
  const uploaded = await uploadFileAttachmentToWeixin({{
    filePath,
    fileName,
    toUserId: target,
    opts: {{ baseUrl: account.baseUrl, token: account.token, timeoutMs }},
    cdnBaseUrl: account.cdnBaseUrl || CDN_BASE_URL,
  }});
  const clientId = `wechat-ai-daily-html-${{Date.now()}}-${{Math.random().toString(16).slice(2)}}`;
  const body = {{
    msg: {{
      from_user_id: '',
      to_user_id: target,
      client_id: clientId,
      message_type: 2,
      message_state: 2,
      item_list: [{{
        type: 4,
        file_item: {{
          media: {{
            encrypt_query_param: uploaded.downloadEncryptedQueryParam,
            aes_key: Buffer.from(uploaded.aeskey).toString('base64'),
            encrypt_type: 1,
          }},
          file_name: fileName,
          len: String(uploaded.fileSize),
        }},
      }}],
      ...(contextToken ? {{ context_token: contextToken }} : {{}}),
    }},
    base_info: buildBaseInfo(),
  }};
  const raw = await apiPostFetch({{
    baseUrl: account.baseUrl,
    endpoint: 'ilink/bot/sendmessage',
    body: JSON.stringify(body),
    token: account.token,
    timeoutMs,
    label: 'WeChatAIDailySummaryHtml',
  }});
  let response = {{}};
  try {{
    response = JSON.parse(raw || '{{}}');
  }} catch {{
    response = {{ raw }};
  }}
  console.log(JSON.stringify({{
    ok: response.ret === 0 && (response.errcode === undefined || response.errcode === 0),
    clientId,
    fileName,
    fileSize: uploaded.fileSize,
    ret: response.ret,
    errcode: response.errcode,
    errmsg: response.errmsg || '',
    hasContextToken: Boolean(contextToken),
    storedContextToken: Boolean(storedContextToken),
    omittedContext: omitContext,
  }}));
}} catch (err) {{
  console.log(JSON.stringify({{
    ok: false,
    error: String(err),
    hasContextToken: Boolean(contextToken),
    storedContextToken: Boolean(storedContextToken),
    omittedContext: omitContext,
  }}));
  process.exitCode = 1;
}}
"""
            command = ["node", "--input-type=module", "-e", script]
            delays = [0, 5, 12]
            last_error: dict | None = None
            omit_context = False
            for attempt, delay in enumerate(delays, start=1):
                if delay:
                    time.sleep(delay)
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                    env={
                        **os.environ,
                        "OPENCLAW_WEIXIN_ACCOUNT_ID": account_id,
                        "OPENCLAW_WEIXIN_TARGET": target,
                        "OPENCLAW_WEIXIN_FILE_PATH": str(file_path),
                        "OPENCLAW_WEIXIN_OMIT_CONTEXT": "1" if omit_context else "0",
                    },
                )
                raw_output = (completed.stdout or completed.stderr or "").strip()
                payload = self._parse_first_json_object(raw_output) if raw_output else {}
                if not payload:
                    payload = {"error": raw_output or f"node exited with code {completed.returncode}"}
                message_id = str(payload.get("clientId") or "")
                if completed.returncode == 0 and payload.get("ok"):
                    return {
                        "sent": bool(message_id),
                        "method": "openclaw-weixin-html-file",
                        "message_id": message_id,
                        "file_name": str(payload.get("fileName") or file_path.name),
                        "file_size": payload.get("fileSize", ""),
                    }
                last_error = {"payload": payload, "message_id": message_id, "attempt": attempt}
                if str(payload.get("ret", "")) == "-2" and payload.get("storedContextToken") and not omit_context:
                    omit_context = True
                    continue
                if str(payload.get("ret", "")) != "-2" or attempt == len(delays):
                    break

            payload = (last_error or {}).get("payload") or {}
            message_id = str((last_error or {}).get("message_id") or "")
            error = str(payload.get("error") or "")
            if not error:
                error = self._format_openclaw_send_error(payload, int((last_error or {}).get("attempt") or 1))
            return {
                "sent": False,
                "method": "openclaw-weixin-html-file",
                "message_id": message_id,
                "error": error,
            }
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "method": "openclaw-weixin-html-file", "error": str(exc)}

    def _send_openclaw_weixin_message(self, account_id: str, target: str, text: str) -> dict:
        direct_result: dict | None = None
        try:
            plugin_api = (
                Path.home()
                / ".openclaw"
                / "npm"
                / "projects"
                / "tencent-weixin-openclaw-weixin-7783ac86ba"
                / "node_modules"
                / "@tencent-weixin"
                / "openclaw-weixin"
                / "dist"
                / "src"
                / "api"
                / "api.js"
            )
            if not plugin_api.exists():
                raise RuntimeError(f"OpenClaw Weixin plugin API not found: {plugin_api}")
            plugin_url = plugin_api.as_posix()
            script = f"""
import fs from 'node:fs';
import {{ apiPostFetch, buildBaseInfo }} from 'file:///{plugin_url}';

const accountId = process.env.OPENCLAW_WEIXIN_ACCOUNT_ID;
const target = process.env.OPENCLAW_WEIXIN_TARGET;
const accountPath = `${{process.env.USERPROFILE}}/.openclaw/openclaw-weixin/accounts/${{accountId}}.json`;
const tokenPath = `${{process.env.USERPROFILE}}/.openclaw/openclaw-weixin/accounts/${{accountId}}.context-tokens.json`;
const account = JSON.parse(fs.readFileSync(accountPath, 'utf8'));
const tokens = fs.existsSync(tokenPath) ? JSON.parse(fs.readFileSync(tokenPath, 'utf8')) : {{}};
const omitContext = process.env.OPENCLAW_WEIXIN_OMIT_CONTEXT === '1';
const storedContextToken = tokens[target] || '';
const contextToken = omitContext ? '' : storedContextToken;
const text = fs.readFileSync(0, 'utf8');
const body = {{
  msg: {{
    from_user_id: '',
    to_user_id: target,
    client_id: `wechat-ai-daily-${{Date.now()}}-${{Math.random().toString(16).slice(2)}}`,
    message_type: 2,
    message_state: 2,
    item_list: [{{ type: 1, text_item: {{ text }} }}],
    ...(contextToken ? {{ context_token: contextToken }} : {{}}),
  }},
  base_info: buildBaseInfo(),
}};

try {{
  const raw = await apiPostFetch({{
    baseUrl: account.baseUrl,
    endpoint: 'ilink/bot/sendmessage',
    body: JSON.stringify(body),
    token: account.token,
    timeoutMs: 15000,
    label: 'WeChatAIDailySummary',
  }});
  let response = {{}};
  try {{
    response = JSON.parse(raw || '{{}}');
  }} catch {{
    response = {{ raw }};
  }}
  console.log(JSON.stringify({{
    ok: response.ret === 0 && (response.errcode === undefined || response.errcode === 0),
    clientId: body.msg.client_id,
    ret: response.ret,
    errcode: response.errcode,
    errmsg: response.errmsg || '',
    hasContextToken: Boolean(contextToken),
    storedContextToken: Boolean(storedContextToken),
    omittedContext: omitContext,
  }}));
}} catch (err) {{
  console.log(JSON.stringify({{
    ok: false,
    error: String(err),
    hasContextToken: Boolean(contextToken),
    storedContextToken: Boolean(storedContextToken),
    omittedContext: omitContext,
  }}));
  process.exitCode = 1;
}}
"""
            command = [
                "node",
                "--input-type=module",
                "-e",
                script,
            ]
            delays = [0, 5, 12, 30]
            last_error: dict | None = None
            omit_context = False
            for attempt, delay in enumerate(delays, start=1):
                if delay:
                    time.sleep(delay)
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    input=text,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=120,
                    env={
                        **os.environ,
                        "OPENCLAW_WEIXIN_ACCOUNT_ID": account_id,
                        "OPENCLAW_WEIXIN_TARGET": target,
                        "OPENCLAW_WEIXIN_OMIT_CONTEXT": "1" if omit_context else "0",
                    },
                )
                if completed.returncode != 0:
                    direct_result = {
                        "sent": False,
                        "method": "openclaw-weixin",
                        "error": (completed.stderr or completed.stdout or "").strip()[-1000:],
                    }
                    break
                payload = json.loads(completed.stdout or "{}")
                message_id = str(payload.get("clientId") or "")
                if payload.get("ok"):
                    return {"sent": bool(message_id), "method": "openclaw-weixin", "message_id": message_id}
                last_error = {"payload": payload, "message_id": message_id, "attempt": attempt}
                if str(payload.get("ret", "")) == "-2" and payload.get("storedContextToken") and not omit_context:
                    omit_context = True
                    continue
                if str(payload.get("ret", "")) != "-2" or attempt == len(delays):
                    break

            if direct_result is None:
                payload = (last_error or {}).get("payload") or {}
                message_id = str((last_error or {}).get("message_id") or "")
                direct_result = {
                    "sent": False,
                    "method": "openclaw-weixin",
                    "message_id": message_id,
                    "error": self._format_openclaw_send_error(payload, int((last_error or {}).get("attempt") or 1)),
                }
        except Exception as exc:  # noqa: BLE001
            direct_result = {"sent": False, "method": "openclaw-weixin", "error": str(exc)}

        direct_error = str(direct_result.get("error") or "")
        if direct_error.startswith("OpenClaw send failed after"):
            return direct_result

        logger.warning(f"Direct iLink send failed, falling back to OpenClaw gateway send: {direct_error}")
        gateway_result = self._send_openclaw_weixin_gateway_message(account_id, target, text)
        if gateway_result.get("sent"):
            return gateway_result
        if direct_result.get("error") and gateway_result.get("error"):
            direct_result["gateway_error"] = gateway_result.get("error", "")
        return direct_result

    def _send_openclaw_weixin_gateway_message(self, account_id: str, target: str, text: str) -> dict:
        try:
            cli_path = Path.home() / "AppData" / "Roaming" / "npm" / "openclaw.cmd"
            completed = subprocess.run(
                [
                    str(cli_path) if cli_path.exists() else "openclaw.cmd",
                    "message",
                    "send",
                    "--channel",
                    "openclaw-weixin",
                    "--account",
                    account_id,
                    "--target",
                    target,
                    "--message",
                    text,
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                cwd=str(Path.home()),
            )
            combined = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            if completed.returncode != 0:
                return {
                    "sent": False,
                    "method": "openclaw-weixin-gateway",
                    "error": combined[-1200:],
                }
            payload = self._parse_first_json_object(combined)
            message_id = str(payload.get("messageId") or (payload.get("payload") or {}).get("messageId") or "")
            if not message_id:
                result = (payload.get("payload") or {}).get("result") or {}
                message_id = str(result.get("messageId") or "")
            if not message_id and "Message ID:" in combined:
                message_id = combined.rsplit("Message ID:", 1)[1].strip().split()[0]
            return {
                "sent": bool(message_id),
                "method": "openclaw-weixin-gateway",
                "message_id": message_id,
                "error": "" if message_id else combined[-1200:],
            }
        except Exception as exc:  # noqa: BLE001
            return {"sent": False, "method": "openclaw-weixin-gateway", "error": str(exc)}

    def _parse_first_json_object(self, text: str) -> dict:
        start = (text or "").find("{")
        if start < 0:
            return {}
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[start:])
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def _format_openclaw_send_error(self, payload: dict, attempts: int) -> str:
        ret = payload.get("ret", "")
        errcode = payload.get("errcode", "")
        errmsg = payload.get("errmsg") or payload.get("error") or ""
        hints = []
        if str(ret) == "-2":
            hints.append("可能被 OpenClaw/微信 iLink 限频或参数拒绝，已退避重试")
        if payload.get("omittedContext"):
            hints.append("已尝试去掉过期 context_token 降级发送")
        if str(ret) == "-14" or str(errcode) == "-14":
            hints.append("OpenClaw 会话过期，需要重新扫码登录")
        if not payload.get("storedContextToken"):
            hints.append("缺少 OpenClaw 会话上下文，请先在微信里给 WeixinClawBot 发一条消息刷新上下文")
        hint = f"；{'；'.join(hints)}" if hints else ""
        return f"OpenClaw send failed after {attempts} attempt(s): ret={ret} errcode={errcode} errmsg={errmsg}{hint}".strip()

    def _split_weixin_text(self, text: str, max_chars: int = 1800) -> list[str]:
        clean = (text or "").strip()
        if len(clean) <= max_chars:
            return [clean]
        parts: list[str] = []
        current: list[str] = []
        current_len = 0
        for paragraph in clean.splitlines():
            line = paragraph.rstrip()
            projected = current_len + len(line) + 1
            if current and projected > max_chars:
                parts.append("\n".join(current).strip())
                current = []
                current_len = 0
            if len(line) > max_chars:
                if current:
                    parts.append("\n".join(current).strip())
                    current = []
                    current_len = 0
                for start in range(0, len(line), max_chars):
                    parts.append(line[start : start + max_chars].strip())
                continue
            current.append(line)
            current_len += len(line) + 1
        if current:
            parts.append("\n".join(current).strip())
        return [part for part in parts if part]

    def _resolve_openclaw_weixin_target(self) -> tuple[str, str]:
        base = Path.home() / ".openclaw" / "openclaw-weixin"
        accounts_path = base / "accounts.json"
        account_ids = json.loads(accounts_path.read_text(encoding="utf-8"))
        if not account_ids:
            raise RuntimeError("OpenClaw Weixin has no configured accounts")
        account_id = str(account_ids[0])
        account = json.loads((base / "accounts" / f"{account_id}.json").read_text(encoding="utf-8"))
        target = str(account.get("userId") or "")
        if not target:
            tokens_path = base / "accounts" / f"{account_id}.context-tokens.json"
            tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
            target = next(iter(tokens.keys()), "")
        if not target:
            raise RuntimeError("OpenClaw Weixin target userId was not found")
        return account_id, target

    async def generate_summary(self, config: DailySummaryConfig | None = None) -> str:
        config = config or await self.get_config()
        db = await get_db()
        hours = config.hours
        messages = await db.get_all_recent_messages(
            hours=hours,
            limit=config.max_messages,
        )
        if not messages:
            return f"最近 {hours} 小时没有同步到新的微信聊天记录。"

        messages = await source_extractor.enrich_messages(db, messages, max_links=80, max_images=20)
        provider = self._get_provider()
        context = build_global_context(messages)
        user_msg = (
            f"请生成最近 {hours} 小时所有微信聊天记录的详细版日报。"
            "这份日报会直接发到微信，但不要过短；目标是让我不用翻聊天记录也能掌握重点。"
            "请至少写 3000 个中文字符，除非聊天记录本身非常少。"
            "必须按群/联系人展开 12-20 个重点对话；每个重点对话写清楚具体内容、关键观点、风险、是否需要我回复。"
            "投资、项目合作、求职、金钱、时间安排、需要我行动的内容优先。"
            "不要只写宽泛分类；要给出具体群名/联系人、日期时间线索、关键词和建议动作。"
            "如果某些对话只是闲聊，请明确标成低优先级。"
        )
        ai_messages = [{"role": "user", "content": f"{context}\n\n{user_msg}"}]
        try:
            summary = await provider.chat(ai_messages, system_prompt=GLOBAL_SUMMARY_SYSTEM_PROMPT)
            if len(summary.strip()) < 2800 and len(messages) > 500:
                expand_msg = (
                    f"{context}\n\n"
                    "下面是刚生成的日报，但太短，不够详细：\n"
                    f"{summary}\n\n"
                    "请基于同一批聊天记录重写为更详细的微信日报。要求："
                    "1. 至少 3000 个中文字符；"
                    "2. 重点对话不少于 12 个；"
                    "3. 每个重点对话写 2-4 条具体信息；"
                    "4. 待办、风险、机会、明天关注事项要更具体；"
                    "5. 不要说空话，不要只概括主题。"
                )
                summary = await provider.chat(
                    [{"role": "user", "content": expand_msg}],
                    system_prompt=GLOBAL_SUMMARY_SYSTEM_PROMPT,
                )
            if len(summary.strip()) < 3500 and len(messages) > 500:
                summary = summary.strip() + "\n\n" + self._local_detail_appendix(messages)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Daily summary AI generation failed: {exc}")
            summary = fallback_global_summary(messages, hours, exc)

        header = f"每日微信总结｜最近 {hours} 小时"
        body = summary.strip() or "今天没有生成有效总结。"
        return f"{header}\n\n{body}"

    def _local_detail_appendix(self, messages: list[dict], max_chats: int = 10) -> str:
        grouped: dict[str, list[dict]] = {}
        names: dict[str, str] = {}
        for msg in messages:
            talker = msg.get("talker") or "unknown"
            grouped.setdefault(talker, []).append(msg)
            names.setdefault(talker, msg.get("remark") or msg.get("nickname") or talker)

        ranked = sorted(
            grouped.items(),
            key=lambda item: (len(item[1]), int(item[1][-1].get("create_time") or 0)),
            reverse=True,
        )[:max_chats]
        lines = ["## 更多会话线索（本地记录补充）", "下面是按活跃度补充的真实聊天片段，方便你回头定位："]
        for talker, items in ranked:
            name = names.get(talker) or talker
            last = items[-1]
            last_time = self._fmt_msg_time(last.get("create_time"))
            lines.append(f"\n### {name}（{len(items)} 条，最后 {last_time}）")
            for msg in items[-5:]:
                speaker = "我" if msg.get("is_sender") else (msg.get("sender") or name)
                content = (msg.get("content") or msg.get("display_content") or f"[{msg.get('type_name') or '消息'}]").strip()
                content = " ".join(content.split())
                if len(content) > 100:
                    content = content[:100] + "..."
                lines.append(f"- {self._fmt_msg_time(msg.get('create_time'))} {speaker}: {content}")
        return "\n".join(lines)

    def _fmt_msg_time(self, value) -> str:
        try:
            return datetime.fromtimestamp(int(value or 0)).strftime("%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return ""

    def _get_provider(self) -> AIProvider:
        settings = get_settings()
        if settings.AI_PROVIDER == "openai":
            return OpenAIProvider()
        return GeminiProvider()

    def _next_run_time(self, now: datetime, time_value: str) -> datetime:
        hour, minute = self._parse_time(time_value)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    def _refresh_next_run_at(self, config: DailySummaryConfig) -> None:
        if not config.enabled:
            self._next_run_at = ""
            return
        self._next_run_at = self._next_run_time(datetime.now(), config.time).isoformat(timespec="seconds")

    async def _wait_for_change_or_stop(self, timeout: float) -> str:
        stop_task = asyncio.create_task(self._stop_event.wait())
        wake_task = asyncio.create_task(self._wake_event.wait())
        done, pending = await asyncio.wait(
            {stop_task, wake_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if not done:
            return "timeout"
        if stop_task in done and stop_task.result():
            return "stop"
        if wake_task in done and wake_task.result():
            self._wake_event.clear()
            return "wake"
        return "timeout"

    def _parse_time(self, value: str) -> tuple[int, int]:
        try:
            hour_s, minute_s = (value or "09:00").strip().split(":", 1)
            hour = min(23, max(0, int(hour_s)))
            minute = min(59, max(0, int(minute_s)))
            return hour, minute
        except Exception:  # noqa: BLE001
            logger.warning(f"Invalid DAILY_SUMMARY_TIME={value!r}; using 09:00")
            return 9, 0

    def _normalize_time(self, value: str) -> str:
        hour, minute = self._parse_time(value)
        return f"{hour:02d}:{minute:02d}"

    def _parse_int(self, value: str, default: int, *, minimum: int, maximum: int) -> int:
        try:
            parsed = int(str(value).strip())
        except (TypeError, ValueError):
            parsed = default
        return min(maximum, max(minimum, parsed))


daily_summary_scheduler = DailySummaryScheduler()
