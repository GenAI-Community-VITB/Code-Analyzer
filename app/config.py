"""Application configuration loaded from environment variables."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", description="Google AI API key for Gemini")
    gemini_model: str = "gemini-2.5-flash"

    max_file_bytes: int = 1_048_576  # 1 MB
    max_files_for_llm: int = 150
    max_chars_per_file_llm: int = 8000
    llm_request_timeout_seconds: float = 120.0

    reports_dir: Path = Field(default=Path("reports"))
    clone_work_dir: Path = Field(default=Path("temp_repos"))
    git_clone_timeout_seconds: float = 600.0
    max_files_clone_analysis: int = 150
    llm_batch_size: int = 8
    llm_concurrent_batches: int = 2

    @field_validator("gemini_api_key", mode="before")
    @classmethod
    def _normalize_gemini_key(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v


def get_settings() -> Settings:
    """Load settings from the environment each call so .env changes apply (no stale cache)."""
    return Settings()
