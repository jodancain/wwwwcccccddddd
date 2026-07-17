from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # AI - Gemini
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-pro-preview"

    # AI - OpenAI compatible
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o"

    # Knowledge-base embeddings. Empty key/base reuse the OpenAI-compatible
    # chat settings, which also works for Zeabur AI Hub.
    EMBEDDING_ENABLED: bool = True
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_BASE_URL: str = ""
    EMBEDDING_MODEL: str = "local-hash-768"
    EMBEDDING_BATCH_SIZE: int = 64
    KNOWLEDGE_REALTIME_MAX_LINKS: int = 24
    KNOWLEDGE_REALTIME_MAX_IMAGES: int = 6

    # AI provider: "gemini" or "openai"
    AI_PROVIDER: str = "gemini"

    # Claude Agent SDK / WeChat entry agent
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_BASE_URL: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-6"
    CLAUDE_AGENT_ENABLED: bool = False
    AGENT_WECHAT_ENTRY_NAME: str = "WeixinClawBot"
    AGENT_PERMISSION_MODE: str = "confirm"
    AGENT_POLL_SECONDS: int = 3
    AGENT_DEV_MODE_ENABLED: bool = False
    AGENT_DEV_WORKSPACE: str = ""
    AGENT_DEV_AUTO_APPLY: bool = False
    AGENT_DEV_MAX_STEPS: int = 8
    AGENT_DEV_COMMAND_TIMEOUT: int = 60
    AGENT_SMART_ROUTER_ENABLED: bool = True
    AGENT_DEV_ENABLE_CLAUDE_CODE_TOOL: bool = True
    CLAUDE_CODE_CLI_PATH: str = ""
    CLAUDE_CODE_MODEL: str = "sonnet"

    # Sync
    SYNC_INTERVAL_SECONDS: int = 7
    DECRYPT_INTERVAL_SECONDS: int = 300

    # Daily WeChat summary scheduler
    DAILY_SUMMARY_ENABLED: bool = False
    DAILY_SUMMARY_TIME: str = "09:00"
    DAILY_SUMMARY_RECEIVER: str = "WeixinClawBot"
    DAILY_SUMMARY_HOURS: int = 24
    DAILY_SUMMARY_MAX_MESSAGES: int = 50000
    DAILY_SUMMARY_SHARE_BASE_URL: str = ""

    # Server
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8090
    INTERNAL_API_LOCAL_ONLY: bool = True
    INTERNAL_API_ALLOWED_CLIENTS: str = "127.0.0.1,::1"

    # Data
    DATA_DIR: str = "./data"

    # Weixin 4.x: override path to db_storage directory.
    # If empty, the decryptor will auto-detect via %APPDATA%\Tencent\xwechat\config.
    WX_DB_DIR: str = ""
    # Weixin 4.x database data key. Optional 64-char hex key captured by an
    # external helper; when set, the decryptor verifies it against each DB.
    WX_DB_KEY: str = ""

    # Inference venv: separate Python env that has torch/transformers/peft/accelerate.
    # Kept separate because the backend venv often lives on a network share where
    # Windows can't load native .pyd DLLs (memory-map over SMB is unsupported).
    # Leave empty to fall back to the backend's own Python interpreter.
    INFERENCE_PYTHON: str = "C:/wechatai-inference-venv/Scripts/python.exe"
    INFERENCE_PORT: int = 8091

    @property
    def data_path(self) -> Path:
        p = Path(self.DATA_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> str:
        return str(self.data_path / "app.db")

    @property
    def decrypted_wx_dir(self) -> Path:
        p = self.data_path / "decrypted_wx_db"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def log_dir(self) -> Path:
        p = self.data_path / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
