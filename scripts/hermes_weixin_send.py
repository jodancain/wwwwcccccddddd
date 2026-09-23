from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a Weixin message through Hermes")
    parser.add_argument("--target", default="", help="Weixin peer ID; defaults to WEIXIN_HOME_CHANNEL")
    parser.add_argument("--media", action="append", default=[], help="Optional local media/document path")
    return parser.parse_args()


async def _send(args: argparse.Namespace) -> dict:
    from hermes_cli.env_loader import load_hermes_dotenv

    load_hermes_dotenv()

    from gateway.platforms.weixin import send_weixin_direct

    message = sys.stdin.buffer.read().decode("utf-8-sig")
    target = (args.target or os.getenv("WEIXIN_HOME_CHANNEL", "")).strip()
    token = os.getenv("WEIXIN_TOKEN", "").strip()
    account_id = os.getenv("WEIXIN_ACCOUNT_ID", "").strip()
    if not target:
        return {"success": False, "error": "WEIXIN_HOME_CHANNEL is not configured"}
    media_files = []
    for item in args.media:
        path = Path(item).expanduser().resolve()
        if not path.is_file():
            return {"success": False, "error": f"Media file not found: {path}"}
        media_files.append((str(path), False))
    result = await send_weixin_direct(
        extra={
            "account_id": account_id,
            "base_url": os.getenv("WEIXIN_BASE_URL", "https://ilinkai.weixin.qq.com"),
            "cdn_base_url": os.getenv("WEIXIN_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c"),
        },
        token=token,
        chat_id=target,
        message=message,
        media_files=media_files,
    )
    if result.get("error"):
        return {"success": False, **result}
    return {"success": bool(result.get("success")), **result}


def main() -> int:
    try:
        result = asyncio.run(_send(_parse_args()))
    except Exception as exc:  # noqa: BLE001
        result = {"success": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
