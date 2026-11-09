"""
Typed settings loaded from environment variables (and an optional local
``.env`` file, which is git-ignored - copy ``.env.example``).

Nothing secret has a default here: the service refuses to start without a
JWT_SECRET_KEY, and refuses to start in production with a known-weak one.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_SERVICE_DIR = Path(__file__).resolve().parent.parent.parent
Environment = Literal["local", "dev", "staging", "production", "test"]

# Values that have appeared in this repo's history / examples. Never valid in production.
_KNOWN_WEAK_SECRETS = {
    ("change" + "-me-in-production"),
    "change-me-to-a-long-random-secret",
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

    # Either DATABASE_URL, or the individual DB_* parts.
    DATABASE_URL: str | None = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str | None = None
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None

    # JWT_SECRET_KEY MUST be shared across all microservices (user_service,
    # operation_service, report_service) since it is what lets those services
    # trust and verify the tokens issued here by user_service.
    JWT_SECRET_KEY: str = Field(min_length=1)
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

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
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"


settings = Settings()
