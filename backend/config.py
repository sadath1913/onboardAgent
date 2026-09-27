"""Application configuration with environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent

# Load .env from the project root.
# Example:
# D:/projects/OnboardAent/.env
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    # ---------------------------------------------------------
    # Application
    # ---------------------------------------------------------

    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))

    data_dir: Path = Path(
        os.getenv("ONBOARD_DATA_DIR", str(ROOT / "data"))
    ).resolve()

    clone_timeout_seconds: int = int(
        os.getenv("CLONE_TIMEOUT_SECONDS", "180")
    )

    # ---------------------------------------------------------
    # PostgreSQL
    # ---------------------------------------------------------

    database_url: str = os.getenv("DATABASE_URL", "")

    # ---------------------------------------------------------
    # Embeddings
    # ---------------------------------------------------------

    embedding_model: str = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )

    embedding_dim: int = int(
        os.getenv("EMBEDDING_DIM", "384")
    )

    retrieval_top_k: int = int(
        os.getenv("RETRIEVAL_TOP_K", "8")
    )

    # ---------------------------------------------------------
    # IBM Bob — Primary LLM
    # ---------------------------------------------------------

    ibm_api_key: str = os.getenv(
        "IBM_API_KEY",
        "",
    )

    ibm_base_url: str = os.getenv(
        "IBM_BASE_URL",
        "",
    )

    ibm_model: str = os.getenv(
        "IBM_MODEL",
        "",
    )

    # ---------------------------------------------------------
    # Groq — Fallback LLM
    # ---------------------------------------------------------

    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_base_url: str = os.getenv(
        "GROQ_BASE_URL",
        "https://api.groq.com/openai/v1",
    )
    groq_model: str = os.getenv(
        "GROQ_MODEL",
        "openai/gpt-oss-20b",
    )
    
    @property
    def repositories_dir(self) -> Path:
        return self.data_dir / "repositories"


settings = Settings()