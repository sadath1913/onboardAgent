"""Application configuration with environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    data_dir: Path = Path(os.getenv("ONBOARD_DATA_DIR", str(ROOT / "data"))).resolve()
    clone_timeout_seconds: int = int(os.getenv("CLONE_TIMEOUT_SECONDS", "180"))
    llm_api_key: str = os.getenv("OPENAI_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "onboard.db"

    @property
    def repositories_dir(self) -> Path:
        return self.data_dir / "repositories"


settings = Settings()
