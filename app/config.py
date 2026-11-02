"""Application settings, loaded from the environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "dev-only-change-me-before-deploying"


class Settings(BaseSettings):
    """Every value here is overridable by an environment variable of the same
    name in upper case (``SECRET_KEY``, ``DATABASE_URL``, ...)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_name: str = "Karkhana"
    environment: str = "development"
    debug: bool = True

    secret_key: str = DEFAULT_SECRET
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    database_url: str = "sqlite:///./karkhana.db"
    cors_origins: str = "*"
    rate_limit_per_minute: int = 120

    default_cost_per_gpu_hour: float = 2.50
    aging_minutes: int = 30
    enable_backfill: bool = True
    allow_demo_seed: bool = True

    @field_validator("environment")
    @classmethod
    def _known_environment(cls, value: str) -> str:
        allowed = {"development", "staging", "production", "test"}
        normalised = value.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"environment must be one of {sorted(allowed)}")
        return normalised

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Fail loudly rather than silently shipping a known secret to production.
    if settings.is_production and settings.secret_key == DEFAULT_SECRET:
        raise RuntimeError(
            "SECRET_KEY is still the development default. Generate one with "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" '
            "and set it in the environment before running in production."
        )
    return settings
