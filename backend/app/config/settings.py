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
    GEMINI_MODEL: str = "gemini-2.5-flash"

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
