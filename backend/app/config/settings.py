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

    # AI provider: "gemini" or "openai"
    AI_PROVIDER: str = "gemini"

    # Sync
    SYNC_INTERVAL_SECONDS: int = 7
    DECRYPT_INTERVAL_SECONDS: int = 60

    # Server
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8080

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
