"""
Typed settings loaded from environment variables (and an optional local
``.env`` file, which is git-ignored - copy ``.env.example``).
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

_SERVICE_DIR = Path(__file__).resolve().parent.parent.parent
Environment = Literal["local", "dev", "staging", "production", "test"]

_KNOWN_WEAK_SECRETS = {
    ("change" + "-me-in-production"),
    ("change" + "-me-to-a-long-random-secret"),
    "dev-only-shared-secret-change-me",
}
_MIN_PRODUCTION_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(_SERVICE_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENVIRONMENT: Environment = "local"

    DATABASE_URL: str | None = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str | None = None
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None

    USER_SERVICE_BASE_URL: str = "http://127.0.0.1:8001"
    OPERATION_SERVICE_BASE_URL: str = "http://127.0.0.1:8002"

    JWT_SECRET_KEY: str = Field(min_length=1)
    JWT_ALGORITHM: str = "HS256"
    SERVICE_TOKEN_EXPIRE_MINUTES: int = 5

    SYNC_PAGE_SIZE: int = 100
    SYNC_TIMEOUT_SECONDS: float = 30.0

    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["*"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, value):
        if isinstance(value, str):
            return [o.strip() for o in value.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _check(self):
        if self.DATABASE_URL and self.DATABASE_URL.startswith("sqlite"):
            raise ValueError("SQLite is strictly prohibited. PostgreSQL is required.")
        if self.DATABASE_URL is None and not (self.DB_NAME and self.DB_USER and self.DB_PASSWORD is not None):
            raise ValueError("Set DATABASE_URL, or DB_NAME / DB_USER / DB_PASSWORD")
        if self.is_production and (
            self.JWT_SECRET_KEY in _KNOWN_WEAK_SECRETS or len(self.JWT_SECRET_KEY) < _MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise ValueError(
                f"JWT_SECRET_KEY is a known placeholder or shorter than {_MIN_PRODUCTION_SECRET_LENGTH} chars; "
                "refusing to start with ENVIRONMENT=production"
            )
        if self.is_production and "*" in self.CORS_ORIGINS:
            raise ValueError("CORS_ORIGINS must list the frontend origin(s) explicitly (not '*') when ENVIRONMENT=production")
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        # URL.create escapes special characters in the password (e.g. '@', '/', '%').
        return URL.create(
            "postgresql+psycopg2",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        ).render_as_string(hide_password=False)


settings = Settings()
