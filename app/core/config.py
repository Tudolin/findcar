from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Everything comes from the environment / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://carwatch:carwatch@localhost:5432/carwatch"
    data_dir: Path = Path("/data")
    timezone: str = "America/Sao_Paulo"
    log_level: str = "INFO"

    # Scheduler (runs inside the app process; keep uvicorn at 1 worker)
    scheduler_enabled: bool = True

    # Polite HTTP
    http_min_delay: float = 3.0
    http_max_delay: float = 5.0
    http_timeout: float = 30.0
    http_max_retries: int = 3
    http_cache_ttl: int = 1800  # seconds
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )

    # FIPE (free public API, optional free token raises the daily quota)
    fipe_base_url: str = "https://fipe.parallelum.com.br/api/v2"
    fipe_token: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "http-cache"


@lru_cache
def get_settings() -> Settings:
    return Settings()
