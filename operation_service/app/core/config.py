"""
Typed settings loaded from environment variables (and an optional local
``.env`` file, which is git-ignored - copy ``.env.example``).
"""

from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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

    # Database
    DATABASE_URL: str | None = None
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str | None = None
    DB_USER: str | None = None
    DB_PASSWORD: str | None = None

    # JWT settings - must match user_service
    JWT_SECRET_KEY: str = Field(min_length=1)
    JWT_ALGORITHM: str = "HS256"

    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["*"]

    # Bakong KHQR & NBC Open API settings
    BAKONG_API_BASE_URL: str = "https://sit-api-bakong.nbc.gov.kh"
    BAKONG_TOKEN: str | None = None
    BAKONG_REGISTERED_EMAIL: str | None = None
    BAKONG_ACCOUNT_ID: str | None = None
    BAKONG_MERCHANT_NAME: str = "Coffee Shop"
    BAKONG_MERCHANT_CITY: str = "Phnom Penh"
    BAKONG_STORE_LABEL: str = "CoffeeShop-Main"
    BAKONG_TERMINAL_LABEL: str = "POS-01"
    BAKONG_PHONE_NUMBER: str | None = None
    PAYMENT_EXPIRY_MINUTES: int = 15
    BAKONG_QR_EXPIRY_SECONDS: int = 900
    BAKONG_MAX_AMOUNT_USD: Decimal = Field(default=Decimal("1.00"))
    BAKONG_USD_TO_KHR_RATE: int = 4100
    BAKONG_CALLBACK_URL: str | None = None
    PUBLIC_BASE_URL: str = "http://localhost:3000"

    # Product image upload settings
    UPLOAD_DIR: str = str(_SERVICE_DIR / "uploads")
    UPLOAD_MAX_BYTES: int = 5 * 1024 * 1024  # 5 MB
    UPLOAD_ALLOWED_TYPES: Annotated[list[str], NoDecode] = [
        "image/jpeg",
        "image/png",
        "image/webp",
    ]
    UPLOAD_MAX_DIMENSION: int = 4000
    UPLOAD_MAX_PIXELS: int = 16_000_000

    @field_validator("CORS_ORIGINS", "UPLOAD_ALLOWED_TYPES", mode="before")
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
        # Fail fast startup check for real Bakong credentials in all non-test runtime environments
        if self.ENVIRONMENT != "test":
            if not self.BAKONG_TOKEN or not self.BAKONG_TOKEN.strip():
                raise ValueError(
                    "Startup validation failed: BAKONG_TOKEN is missing or empty. "
                    "Configure BAKONG_TOKEN in .env before starting the service."
                )
            if not self.BAKONG_ACCOUNT_ID or not self.BAKONG_ACCOUNT_ID.strip():
                raise ValueError(
                    "Startup validation failed: BAKONG_ACCOUNT_ID is missing or empty. "
                    "Configure BAKONG_ACCOUNT_ID in .env before starting the service."
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
