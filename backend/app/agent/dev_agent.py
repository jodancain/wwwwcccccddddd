from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from app.config.settings import get_settings
from app.storage.database import AppDatabase


DEV_AGENT_VERSION = "dev-agent-2026-06-23-claude-code-planner-v6"


DEV_SYSTEM_PROMPT = """你是 WeChatAI 的本地开发 Agent，目标是像 Codex 一样帮助用户改进这个项目。

工作方式：
1. 先理解用户要的结果，不要只解释不能做。
2. 你可以读取项目文件、搜索代码、提出文件修改和命令执行。
3. 读取和搜索可以直接做；写文件、运行命令必须通过待确认动作，除非 AGENT_DEV_AUTO_APPLY=true。
4. 对危险或破坏性操作保持克制：删除数据、重置 git、泄露密钥、上传隐私数据都不能做。
5. 回复要直接说明已经检查了什么、准备改什么、下一步需要用户确认什么。
6. 如果用户要求的是产品能力，例如“每天自动发送总结”，你应该把它当成开发任务，找到项目中合适的位置并提出可执行修改。
7. 对代码修改优先使用 propose_patch 生成小型 unified diff；不要只给方案。如果工具返回错误，修正输入后继续，直到形成可确认动作或明确说明缺少什么信息。
8. 对复杂开发任务，优先调用 claude_code_plan 获取 Claude Code 的只读项目规划；如果该工具不可用，继续使用 list_files/read_file/search_text 自己完成，不要卡住。
"""


EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "data",
    ".pytest_cache",
}

DANGEROUS_COMMAND_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bRemove-Item\b.*\b-Recurse\b",
    r"\bdel\s+/[sq]\b",
    r"\brmdir\s+/[sq]\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\b",
    r"\bformat\b",
]


@dataclass
class DevAgentResult:
    text: str
    used_claude: bool = False
    pending_actions: list[dict] | None = None


def is_dev_request(message: str) -> bool:
    compact = re.sub(r"\s+", "", message.lower())
    if compact.startswith(("/dev", "dev:", "开发:", "开发：")):
        return True
    schedule_words = ("每天", "每日", "定时", "计划任务", "自动", "以后")
    delivery_words = ("发总结", "发送总结", "给我发", "推送", "主动发", "自动总结")
    if any(word in compact for word in schedule_words) and any(word in compact for word in delivery_words):
        return True
    keywords = (
        "像codex",
        "自我开发",
        "自己开发",
        "开发功能",
        "加功能",
        "新增功能",
        "改代码",
        "修改代码",
        "修复",
        "bug",
        "报错",
        "打包",
        "重启",
        "运行测试",
        "自动发送",
        "自动给我发",
        "自动发总结",
        "每天发总结",
        "每日总结",
        "定时总结",
        "主动推送",
        "定时",
        "计划任务",
        "scheduler",
        "automation",
    )
    return any(k in compact for k in keywords)


def _truncate(text: str, limit: int = 6000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated {len(text) - limit} chars]"


def _is_dangerous_command(command: str) -> bool:
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in DANGEROUS_COMMAND_PATTERNS)


def _is_dangerous_patch(patch: str) -> bool:
    return "deleted file mode" in patch or re.search(r"(?m)^\+\+\+ /dev/null$", patch) is not None


class DevAgent:
    def __init__(self, db: AppDatabase):
        self.db = db
        self.settings = get_settings()
        self.workspace = self._resolve_workspace()

    def _resolve_workspace(self) -> Path:
        configured = getattr(self.settings, "AGENT_DEV_WORKSPACE", "") or ""
        root = Path(configured).expanduser() if configured.strip() else Path.cwd()
        return root.resolve()

    def _resolve_path(self, raw_path: str) -> Path:
        rel = Path(raw_path.strip().replace("\\", os.sep))
        path = rel if rel.is_absolute() else self.workspace / rel
        resolved = path.resolve()
        try:
            common = os.path.commonpath([str(self.workspace), str(resolved)])
        except ValueError as exc:
            raise ValueError(f"path outside workspace: {raw_path}") from exc
        if common != str(self.workspace):
            raise ValueError(f"path outside workspace: {raw_path}")
        return resolved

    async def reply(self, message: str) -> DevAgentResult:
        if not getattr(self.settings, "AGENT_DEV_MODE_ENABLED", False):
            return DevAgentResult(
                "开发模式没有开启。要让我像 Codex 一样读项目、改代码、运行命令，请在 .env 里设置：\n"
                "AGENT_DEV_MODE_ENABLED=true\n"
                f"AGENT_DEV_WORKSPACE={self.workspace}"
            )
        if not self.settings.ANTHROPIC_API_KEY or not self.settings.ANTHROPIC_BASE_URL:
            return DevAgentResult("开发模式需要 ANTHROPIC_API_KEY 和 ANTHROPIC_BASE_URL，当前没有配置完整。")

        try:
            text, pending = await self._run_tool_loop(message)
            return DevAgentResult(text=text, used_claude=True, pending_actions=pending)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"DevAgent failed: {exc}")
            return DevAgentResult(f"开发 Agent 执行失败：{exc}")

    def _is_valid_pending_action(self, action: dict[str, Any]) -> bool:
        action_type = action.get("action_type")
        payload = action.get("payload") or {}
        if action_type == "dev_write_files":
            changes = payload.get("changes")
            return isinstance(changes, list) and any(
                isinstance(item, dict)
                and str(item.get("path") or "").strip()
                and str(item.get("content") or "").strip()
                for item in changes
            )
        if action_type == "dev_apply_patch":
            return bool(str(payload.get("patch") or "").strip())
        if action_type == "dev_shell_command":
            return bool(str(payload.get("command") or "").strip())
        return True

    async def _run_tool_loop(self, message: str) -> tuple[str, list[dict]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"用户需求：{message}\n\n"
                    f"工作区：{self.workspace}\n"
                    "请像 Codex 一样先检查项目，再提出可执行结果。"
                ),
            }
        ]
        pending_actions: list[dict] = []
        last_text = ""

        for _ in range(max(1, int(getattr(self.settings, "AGENT_DEV_MAX_STEPS", 8)))):
            payload = {
                "model": self.settings.CLAUDE_MODEL,
                "max_tokens": 2400,
                "system": DEV_SYSTEM_PROMPT,
                "messages": messages,
                "tools": self._tool_specs(),
            }
            response = await asyncio.to_thread(self._anthropic_messages, payload)
            content = response.get("content") or []
            text_parts = [str(b.get("text")) for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            if text_parts:
                last_text = "\n".join(text_parts).strip()

            tool_uses = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
            if not tool_uses:
                if pending_actions:
                    ids = ", ".join(a.get("id", "") for a in pending_actions)
                    suffix = f"\n\n已创建待确认动作：{ids}\n回复 `确认 {pending_actions[0].get('id', '')}` 执行，或继续告诉我调整。"
                    return ((last_text or "已生成可确认开发动作。") + suffix, pending_actions)
                return (last_text or "开发 Agent 没有生成可执行回复。", pending_actions)

            messages.append({"role": "assistant", "content": content})
            tool_results = []
            for tool_use in tool_uses:
                result = await self._run_tool(str(tool_use.get("name") or ""), tool_use.get("input") or {})
                if isinstance(result, dict) and result.get("pending_action"):
                    action = result["pending_action"]
                    if self._is_valid_pending_action(action):
                        pending_actions.append(action)
                    else:
                        action_id = str(action.get("id") or "")
                        if action_id:
                            await self.db.update_pending_action_status(action_id, "failed")
                        result = {"ok": False, "error": "invalid empty pending action rejected"}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.get("id"),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        if pending_actions:
            ids = ", ".join(a.get("id", "") for a in pending_actions)
            return (last_text + f"\n\n已创建待确认动作：{ids}\n回复 `confirm <id>` 执行。", pending_actions)
        return (last_text or "开发 Agent 已达到最大步骤数，但没有形成最终结果。", pending_actions)

    def _anthropic_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self.settings.ANTHROPIC_BASE_URL.rstrip("/") + "/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise RuntimeError(f"Anthropic HTTP {exc.code}: {detail}") from exc

    def _tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "list_files",
                "description": "List files under the workspace. Excludes dependency/build/data folders.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path under workspace."},
                        "max_entries": {"type": "integer", "default": 120},
                    },
                },
            },
            {
                "name": "read_file",
                "description": "Read a UTF-8 text file inside the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 12000},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "search_text",
                "description": "Search text in workspace files using ripgrep when available.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "default": "."},
                        "max_matches": {"type": "integer", "default": 80},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "claude_code_plan",
                "description": "Ask Claude Code / Claude Agent SDK to inspect the workspace in read-only plan mode. It cannot edit files or run shell commands.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                        "max_chars": {"type": "integer", "default": 6000},
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "propose_file_changes",
                "description": "Create a pending action to write one or more files. The user must confirm before writes unless auto-apply is enabled.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "changes": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "path": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["path", "content"],
                            },
                        },
                    },
                    "required": ["reason", "changes"],
                },
            },
            {
                "name": "propose_patch",
                "description": "Create a pending action to apply a unified diff with git apply. Prefer this for focused code edits.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "patch": {
                            "type": "string",
                            "description": "Unified diff, usually starting with diff --git.",
                        },
                    },
                    "required": ["reason", "patch"],
                },
            },
            {
                "name": "propose_shell_command",
                "description": "Create a pending action to run a shell command in the workspace.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string"},
                        "command": {"type": "string"},
                        "timeout_seconds": {"type": "integer", "default": 60},
                    },
                    "required": ["reason", "command"],
                },
            },
        ]

    async def _run_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "list_files":
                return self._list_files(str(args.get("path") or "."), int(args.get("max_entries") or 120))
            if name == "read_file":
                return self._read_file(str(args.get("path") or ""), int(args.get("max_chars") or 12000))
            if name == "search_text":
                return self._search_text(
                    str(args.get("query") or ""),
                    str(args.get("path") or "."),
                    int(args.get("max_matches") or 80),
                )
            if name == "claude_code_plan":
                return await self._claude_code_plan(
                    str(args.get("prompt") or ""),
                    int(args.get("max_chars") or 6000),
                )
            if name == "propose_file_changes":
                return await self._propose_file_changes(str(args.get("reason") or ""), args.get("changes") or [])
            if name == "propose_patch":
                return await self._propose_patch(str(args.get("reason") or ""), str(args.get("patch") or ""))
            if name == "propose_shell_command":
                return await self._propose_shell_command(
                    str(args.get("reason") or ""),
                    str(args.get("command") or ""),
                    int(args.get("timeout_seconds") or getattr(self.settings, "AGENT_DEV_COMMAND_TIMEOUT", 60)),
                )
            return {"ok": False, "error": f"unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def _list_files(self, raw_path: str, max_entries: int) -> dict[str, Any]:
        root = self._resolve_path(raw_path)
        if root.is_file():
            return {"ok": True, "files": [str(root.relative_to(self.workspace))]}
        files: list[str] = []
        for current, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".cache")]
            for name in names:
                rel = str((Path(current) / name).relative_to(self.workspace))
                files.append(rel)
                if len(files) >= max_entries:
                    return {"ok": True, "files": files, "truncated": True}
        return {"ok": True, "files": files, "truncated": False}

    def _read_file(self, raw_path: str, max_chars: int) -> dict[str, Any]:
        path = self._resolve_path(raw_path)
        if not path.exists() or not path.is_file():
            return {"ok": False, "error": f"file not found: {raw_path}"}
        data = path.read_text(encoding="utf-8", errors="replace")
        return {"ok": True, "path": str(path.relative_to(self.workspace)), "content": _truncate(data, max_chars)}

    def _search_text(self, query: str, raw_path: str, max_matches: int) -> dict[str, Any]:
        root = self._resolve_path(raw_path)
        if not query.strip():
            return {"ok": False, "error": "empty query"}
        rg = shutil.which("rg")
        if rg:
            command = [
                rg,
                "-n",
                "--hidden",
                "-g",
                "!node_modules",
                "-g",
                "!dist",
                "-g",
                "!build",
                "-g",
                "!data",
                query,
                str(root),
            ]
            proc = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            lines = proc.stdout.splitlines()[:max_matches]
            return {"ok": True, "matches": lines, "truncated": len(proc.stdout.splitlines()) > len(lines)}

        matches: list[str] = []
        for current, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
            for name in names:
                path = Path(current) / name
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for idx, line in enumerate(text.splitlines(), 1):
                    if query in line:
                        matches.append(f"{path.relative_to(self.workspace)}:{idx}:{line[:300]}")
                        if len(matches) >= max_matches:
                            return {"ok": True, "matches": matches, "truncated": True}
        return {"ok": True, "matches": matches, "truncated": False}

    async def _claude_code_plan(self, prompt: str, max_chars: int) -> dict[str, Any]:
        if not getattr(self.settings, "AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL", True):
            return {"ok": False, "error": "Claude Code planner is disabled"}
        if not prompt.strip():
            return {"ok": False, "error": "empty prompt"}

        cli_path = (getattr(self.settings, "CLAUDE_CODE_CLI_PATH", "") or "").strip()
        if not cli_path:
            found = shutil.which("claude")
            if found:
                cli_path = found
            else:
                candidate = Path.home() / ".local" / "bin" / "claude.exe"
                if candidate.exists():
                    cli_path = str(candidate)
        if not cli_path:
            return {"ok": False, "error": "Claude Code CLI not found"}

        if not self.settings.ANTHROPIC_API_KEY:
            return {"ok": False, "error": "ANTHROPIC_API_KEY is not configured"}

        try:
            text = await self._run_claude_agent_sdk_plan(prompt, cli_path)
            if text:
                return {"ok": True, "engine": "claude_agent_sdk", "text": _truncate(text, max_chars)}
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Claude Agent SDK planner failed: {exc}")
            fallback = await self._anthropic_planner_fallback(prompt, max_chars)
            if fallback.get("ok"):
                fallback["claude_code_error"] = str(exc)
                return fallback
            return {
                "ok": False,
                "engine": "claude_agent_sdk",
                "error": str(exc),
                "fallback": fallback.get("error") or "Use built-in list_files/read_file/search_text tools.",
            }
        return {"ok": False, "error": "Claude Code planner returned no text"}

    async def _run_claude_agent_sdk_plan(self, prompt: str, cli_path: str) -> str:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            from claude_code_sdk import ClaudeCodeOptions as ClaudeAgentOptions, query  # type: ignore

        sdk_env = {"ANTHROPIC_API_KEY": self.settings.ANTHROPIC_API_KEY}
        if self.settings.ANTHROPIC_BASE_URL:
            sdk_env["ANTHROPIC_BASE_URL"] = self.settings.ANTHROPIC_BASE_URL

        options = ClaudeAgentOptions(
            cwd=str(self.workspace),
            cli_path=cli_path,
            permission_mode="plan",
            allowed_tools=["Read", "Glob", "Grep"],
            max_turns=1,
            model=getattr(self.settings, "CLAUDE_CODE_MODEL", "sonnet") or "sonnet",
            env=sdk_env,
            setting_sources=[],
            system_prompt=(
                "You are Claude Code running inside WeChatAI as a read-only planner. "
                "Inspect the project and produce a concise implementation plan. "
                "Do not edit files, run shell commands, reveal secrets, or ask for broad permissions."
            ),
        )
        chunks: list[str] = []
        async for item in query(prompt=prompt, options=options):
            text = self._sdk_message_text(item)
            if text:
                chunks.append(text)
        return "\n".join(chunks).strip()

    async def _anthropic_planner_fallback(self, prompt: str, max_chars: int) -> dict[str, Any]:
        if not self.settings.ANTHROPIC_BASE_URL:
            return {"ok": False, "error": "ANTHROPIC_BASE_URL is not configured for planner fallback"}
        body = {
            "model": self.settings.CLAUDE_MODEL,
            "max_tokens": min(max(max_chars // 2, 800), 2400),
            "system": (
                "You are a Claude Code-style read-only planner embedded in WeChatAI. "
                "Use the project context from the user's prompt and return a concise implementation plan. "
                "Do not claim that files were edited. Do not reveal secrets."
            ),
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Workspace: {self.workspace}\n\n"
                        f"User planning request:\n{prompt}\n\n"
                        "If exact files are needed, say which files the main DevAgent should inspect next."
                    ),
                }
            ],
        }
        req = urllib.request.Request(
            self.settings.ANTHROPIC_BASE_URL.rstrip("/") + "/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.settings.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            return {"ok": False, "error": f"planner fallback HTTP {exc.code}: {detail}"}
        parts = [
            str(block.get("text") or "")
            for block in payload.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(parts).strip()
        if not text:
            return {"ok": False, "error": "planner fallback returned no text"}
        return {"ok": True, "engine": "anthropic_messages_fallback", "text": _truncate(text, max_chars)}

    def _sdk_message_text(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        content = getattr(item, "content", None)
        if not content:
            return ""
        parts = []
        for block in content if isinstance(content, list) else [content]:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
            elif isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts).strip()

    async def _propose_file_changes(self, reason: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
        if not changes:
            return {"ok": False, "error": "no file changes provided; include at least one path and full content"}
        normalized = []
        for item in changes:
            raw_path = str(item.get("path") or "").strip()
            if not raw_path:
                return {"ok": False, "error": "file change missing path"}
            content = str(item.get("content") or "")
            path = self._resolve_path(raw_path)
            normalized.append({"path": str(path.relative_to(self.workspace)), "content": content})
        payload = {"workspace": str(self.workspace), "reason": reason, "changes": normalized}
        if getattr(self.settings, "AGENT_DEV_AUTO_APPLY", False):
            result = await execute_dev_action_payload("dev_write_files", payload)
            return {"ok": True, "auto_applied": True, "result": result}
        action = await self._create_pending("dev_write_files", payload)
        return {"ok": True, "pending_action": action, "message": f"created pending write action {action['id']}"}

    async def _propose_patch(self, reason: str, patch: str) -> dict[str, Any]:
        patch = patch.strip()
        if not patch:
            return {"ok": False, "error": "empty patch"}
        if "diff --git " not in patch and not re.search(r"(?m)^--- .+\n\+\+\+ ", patch):
            return {"ok": False, "error": "patch must be a unified diff"}
        if _is_dangerous_patch(patch):
            return {"ok": False, "error": "patch deletes files; destructive patch refused"}
        payload = {"workspace": str(self.workspace), "reason": reason, "patch": patch}
        if getattr(self.settings, "AGENT_DEV_AUTO_APPLY", False):
            result = await execute_dev_action_payload("dev_apply_patch", payload)
            return {"ok": True, "auto_applied": True, "result": result}
        action = await self._create_pending("dev_apply_patch", payload)
        return {"ok": True, "pending_action": action, "message": f"created pending patch action {action['id']}"}

    async def _propose_shell_command(self, reason: str, command: str, timeout_seconds: int) -> dict[str, Any]:
        if not command.strip():
            return {"ok": False, "error": "empty command"}
        if _is_dangerous_command(command):
            return {"ok": False, "error": "dangerous command refused"}
        payload = {
            "workspace": str(self.workspace),
            "reason": reason,
            "command": command,
            "timeout_seconds": min(max(timeout_seconds, 1), 300),
        }
        if getattr(self.settings, "AGENT_DEV_AUTO_APPLY", False):
            result = await execute_dev_action_payload("dev_shell_command", payload)
            return {"ok": True, "auto_applied": True, "result": result}
        action = await self._create_pending("dev_shell_command", payload)
        return {"ok": True, "pending_action": action, "message": f"created pending command action {action['id']}"}

    async def _create_pending(self, action_type: str, payload: dict[str, Any]) -> dict:
        if action_type == "dev_write_files" and not payload.get("changes"):
            raise ValueError("refusing to create empty file-change action")
        if action_type == "dev_apply_patch" and not str(payload.get("patch") or "").strip():
            raise ValueError("refusing to create empty patch action")
        action_id = str(uuid.uuid4())[:8]
        return await self.db.create_pending_action(action_id, action_type, payload)


async def execute_dev_action_payload(action_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(str(payload.get("workspace") or Path.cwd())).resolve()
    if action_type == "dev_write_files":
        if not payload.get("changes"):
            raise ValueError("refusing to execute empty file-change action")
        written = []
        for item in payload.get("changes") or []:
            rel = Path(str(item.get("path") or ""))
            if rel.is_absolute():
                raise ValueError("absolute paths are not allowed in write payload")
            path = (workspace / rel).resolve()
            if os.path.commonpath([str(workspace), str(path)]) != str(workspace):
                raise ValueError(f"path outside workspace: {rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(item.get("content") or ""), encoding="utf-8")
            written.append(str(rel))
        return {"status": "written", "files": written}

    if action_type == "dev_apply_patch":
        patch = str(payload.get("patch") or "").strip()
        if not patch:
            raise ValueError("refusing to execute empty patch action")
        if _is_dangerous_patch(patch):
            raise ValueError("patch deletes files; destructive patch refused")
        if not shutil.which("git"):
            raise ValueError("git is not available to apply patch")
        proc = await asyncio.create_subprocess_exec(
            "git",
            "apply",
            "--whitespace=nowarn",
            "-",
            cwd=str(workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(patch.encode("utf-8")), timeout=120)
        stdout = stdout_b.decode("utf-8", "replace")
        stderr = stderr_b.decode("utf-8", "replace")
        return {
            "status": "applied" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout": _truncate(stdout, 4000),
            "stderr": _truncate(stderr, 4000),
        }

    if action_type == "dev_shell_command":
        command = str(payload.get("command") or "")
        if _is_dangerous_command(command):
            raise ValueError("dangerous command refused")
        timeout = min(max(int(payload.get("timeout_seconds") or 60), 1), 300)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_b.decode("utf-8", "replace")
        stderr = stderr_b.decode("utf-8", "replace")
        return {
            "status": "completed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "stdout": _truncate(stdout, 4000),
            "stderr": _truncate(stderr, 4000),
        }

    raise ValueError(f"unsupported dev action: {action_type}")
