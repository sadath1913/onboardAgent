"""Tests for the hybrid retrieval pipeline.

These tests verify the tokenization and excerpt helpers without requiring
a live PostgreSQL connection. Full RAG pipeline tests require DATABASE_URL.
"""
import unittest
from unittest.mock import MagicMock, patch

from backend.retrieval import _tokens, _excerpt, hybrid_search


class TokenizerTests(unittest.TestCase):
    def test_tokens_splits_camel_case_and_removes_stop_words(self):
        tokens = _tokens("where is validate token checked")
        self.assertIn("validate", tokens)
        self.assertIn("token", tokens)
        self.assertNotIn("where", tokens)
        self.assertNotIn("is", tokens)

    def test_tokens_empty_query(self):
        self.assertEqual(_tokens(""), [])

    def test_tokens_stop_words_only(self):
        self.assertEqual(_tokens("where the and is"), [])


class ExcerptTests(unittest.TestCase):
    def test_excerpt_centers_on_matching_term(self):
        content = "\n".join([f"line {i}" for i in range(100)])
        content += "\nvalidate_token is here"
        start, end, excerpt = _excerpt(content, ["validate_token"])
        self.assertIn("validate_token", excerpt)
        self.assertGreater(end, start)

    def test_excerpt_with_preferred_line(self):
        content = "line1\nline2\nline3\nline4\nline5"
        start, end, excerpt = _excerpt(content, [], preferred_line=3)
        self.assertIn("line3", excerpt)

    def test_excerpt_truncates_long_lines(self):
        long_line = "x" * 600
        start, end, excerpt = _excerpt(long_line, ["x"])
        self.assertIn("[line truncated]", excerpt)


class HybridSearchTests(unittest.TestCase):
    """Tests for hybrid_search behavior with mocked database responses."""

    def test_empty_query_returns_empty(self):
        """Empty query should return empty results."""
        with patch("backend.retrieval.keyword_search", return_value=[]), \
             patch("backend.retrieval.symbol_search", return_value=[]), \
             patch("backend.retrieval.vector_search", return_value=[]):
            results = hybrid_search("repo123", "", top_k=8)
            self.assertEqual(results, [])

    def test_hybrid_search_merges_results(self):
        """Hybrid search should merge and deduplicate vector + keyword + symbol results."""
        mock_chunk = {
            "chunk_id": "chunk1",
            "path": "app/auth.py",
            "chunk_text": "def validate_token(token):\n    return token == 'ok'",
            "chunk_type": "function",
            "start_line": 1,
            "end_line": 3,
            "language": "python",
            "symbol_name": "validate_token",
            "similarity": 0.95,
            "reason": "vector similarity",
        }
        with patch("backend.embeddings.embed_query", return_value=[0.1] * 384), \
             patch("backend.retrieval.vector_search", return_value=[mock_chunk]), \
             patch("backend.retrieval.keyword_search", return_value=[]), \
             patch("backend.retrieval.symbol_search", return_value=[]), \
             patch("backend.retrieval._expand_by_relationships", return_value=[]):
            results = hybrid_search("repo123", "validate token", top_k=8)
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "app/auth.py")
            self.assertEqual(results[0]["reason"], "vector similarity")

    def test_hybrid_search_fallback_to_keyword_when_vector_fails(self):
        """Should fall back to keyword results when vector search raises."""
        mock_chunk = {
            "chunk_id": "chunk2",
            "path": "app/routes.py",
            "chunk_text": "from app.auth import validate_token",
            "chunk_type": "module",
            "start_line": 1,
            "end_line": 2,
            "language": "python",
            "symbol_name": None,
            "similarity": 0.5,
            "reason": "keyword match",
        }
        with patch("backend.embeddings.embed_query", side_effect=RuntimeError("no model")), \
             patch("backend.retrieval.keyword_search", return_value=[mock_chunk]), \
             patch("backend.retrieval.symbol_search", return_value=[]), \
             patch("backend.retrieval._expand_by_relationships", return_value=[]):
            results = hybrid_search("repo123", "validate token", top_k=8)
            self.assertTrue(results)
            self.assertEqual(results[0]["path"], "app/routes.py")


if __name__ == "__main__":
    unittest.main()
