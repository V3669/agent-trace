from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTTRACE_", env_file=".env", extra="ignore")

    port: int = 8788
    upstream_anthropic: str = "https://api.anthropic.com"
    upstream_openai: str = "https://api.openai.com"
    db_path: Path = Path("~/.agenttrace/store.db")
    store_bodies: bool = True
    retention_days: int = 30
    queue_maxsize: int = 10_000
    price_table: Path | None = None
    session_gap_seconds: int = 1800

    @field_validator("db_path", mode="before")
    @classmethod
    def expand_db_path(cls, v: object) -> Path:
        return Path(str(v)).expanduser()


settings = Settings()
