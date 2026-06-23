from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def _runtime_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _bundle_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[1]


def _ensure_env_file(runtime_dir: Path) -> None:
    env_file = runtime_dir / ".env"
    if env_file.exists():
        return

    local_example = runtime_dir / ".env.example"
    bundled_example = _bundle_dir() / ".env.example"
    if local_example.exists():
        shutil.copyfile(local_example, env_file)
    elif bundled_example.exists():
        shutil.copyfile(bundled_example, env_file)
    else:
        env_file.write_text(
            "\n".join(
                [
                    "AI_PROVIDER=openai",
                    "OPENAI_API_KEY=your_openai_api_key_here",
                    "OPENAI_BASE_URL=https://api.openai.com/v1",
                    "OPENAI_MODEL=gpt-4o",
                    "APP_HOST=127.0.0.1",
                    "APP_PORT=8090",
                    "DATA_DIR=./data",
                    "CLAUDE_AGENT_ENABLED=false",
                    "ANTHROPIC_API_KEY=",
                    "ANTHROPIC_BASE_URL=",
                    "CLAUDE_MODEL=claude-sonnet-4-6",
                ]
            ),
            encoding="utf-8",
        )


def _open_browser_later(url: str) -> None:
    def run() -> None:
        time.sleep(2.5)
        webbrowser.open(url)

    threading.Thread(target=run, daemon=True).start()


def main() -> None:
    runtime_dir = _runtime_dir()
    os.chdir(runtime_dir)
    (runtime_dir / "data").mkdir(exist_ok=True)
    _ensure_env_file(runtime_dir)

    from app.config.settings import get_settings
    from app.main import app

    settings = get_settings()
    url = f"http://{settings.APP_HOST}:{settings.APP_PORT}"

    print("=" * 58)
    print("WeChatAI portable is starting")
    print(f"Config: {runtime_dir / '.env'}")
    print(f"Data:   {runtime_dir / 'data'}")
    print(f"URL:    {url}")
    print("=" * 58)
    print("Keep this window open while using WeChatAI.")
    print("Press Ctrl+C or close the window to stop the server.")
    print()

    _open_browser_later(url)
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT, reload=False)


if __name__ == "__main__":
    main()
