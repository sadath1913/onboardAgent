"""Transformers-based embedding generation for repository chunks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.config import settings

LOGGER = logging.getLogger("onboard.embeddings")

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            LOGGER.info("loading embedding model model=%s", settings.embedding_model)
            _model = SentenceTransformer(settings.embedding_model)
            LOGGER.info("embedding model loaded dim=%d", settings.embedding_dim)
        except Exception as exc:
            LOGGER.error("failed to load embedding model: %s", exc)
            raise
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of texts and return normalized float vectors."""
    if not texts:
        return []
    model = _get_model()
    embeddings = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
    return [emb.tolist() for emb in embeddings]


def embed_query(query: str) -> list[float]:
    """Embed a single query string."""
    results = embed_texts([query])
    return results[0] if results else []
