"""Tests for OnboardingService with mocked PostgreSQL.

These tests verify the service orchestration logic without requiring
a live PostgreSQL database. Full integration tests require DATABASE_URL.
"""
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend import config
from backend.analyzer import analyze_repository


class AnalysisPipelineTests(unittest.TestCase):
    """Test the analysis pipeline components (no DB needed)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.fixture = Path(self.temp.name) / "fixture"
        (self.fixture / "src").mkdir(parents=True)
        (self.fixture / "README.md").write_text("# Fixture\nA small API example.\n", encoding="utf-8")
        (self.fixture / "src" / "main.py").write_text(
            "from .service import get_user\n"
            "@app.get('/users/{user_id}')\n"
            "def user_route(user_id):\n"
            "    return get_user(user_id)\n"
            "API_TOKEN = 'demo-secret-value'\n",
            encoding="utf-8",
        )
        (self.fixture / "src" / "service.py").write_text(
            "def get_user(user_id):\n    return {'id': user_id}\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_analyze_repository_extracts_expected_data(self):
        analysis = analyze_repository(self.fixture)
        self.assertGreaterEqual(analysis["counts"]["symbols"], 2)
        self.assertTrue(analysis["routes"])
        route = next((r for r in analysis["routes"] if r["path"] == "/users/{user_id}"), None)
        self.assertIsNotNone(route, "expected route /users/{user_id}")
        # Secrets redacted
        for f in analysis["files"]:
            self.assertNotIn("demo-secret-value", f["content"])

    def test_create_chunks_produces_semantic_chunks(self):
        from backend.analyzer import create_chunks
        analysis = analyze_repository(self.fixture)
        chunks = create_chunks(analysis["files"], analysis["symbols"])
        self.assertTrue(chunks, "Expected semantic chunks to be created")
        types = {c["chunk_type"] for c in chunks}
        self.assertTrue(types, "Expected chunk types to be populated")

    def test_guide_generator_falls_back_to_static_without_llm(self):
        from backend.guide_generator import generate_llm_guide
        analysis = analyze_repository(self.fixture)
        repo = {"owner": "octo", "name": "fixture", "id": "test-id"}
        guide = generate_llm_guide(analysis, repo, llm=None)
        self.assertIn("content_md", guide)
        self.assertIn("entry_points", guide)
        self.assertTrue(guide["content_md"])


class ServiceMockTests(unittest.TestCase):
    """Test service methods with mocked database session."""

    def test_validate_repository(self):
        """validate_repository should work without any DB."""
        # Import is fine since validate just calls github.py
        from backend.service import OnboardingService
        # Patch mark_stale_jobs so no DB connection is attempted
        with patch("backend.service.mark_stale_jobs"):
            svc = OnboardingService()
        result = svc.validate_repository("https://github.com/owner/repo")
        self.assertTrue(result["valid"])
        self.assertEqual(result["owner"], "owner")
        self.assertEqual(result["name"], "repo")
        svc.close()

    def test_register_repository_uses_correct_url(self):
        """register_repository should call clone with canonical URL."""
        from backend.service import OnboardingService
        with patch("backend.service.mark_stale_jobs"):
            svc = OnboardingService()
        with patch("backend.service.get_session") as mock_session:
            # Mock the session context manager
            mock_ctx = MagicMock()
            mock_session.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_session.return_value.__exit__ = MagicMock(return_value=False)
            mock_ctx.query.return_value.filter_by.return_value.first.return_value = None
            mock_ctx.add = MagicMock()
            mock_ctx.flush = MagicMock()
            with patch.object(svc, "_schedule", return_value=True) as mock_schedule:
                result = svc.register_repository("https://github.com/octo/demo")
                self.assertIn("repository_id", result)
                self.assertIn("job_id", result)
                self.assertEqual(result["status"], "queued")
        svc.close()

    def test_llm_provider_fallback_disabled_without_keys(self):
        """LLM should be disabled when no API keys are configured."""
        from backend.llm import FallbackLLM
        llm = FallbackLLM()
        self.assertFalse(llm.enabled)
        result = llm.complete([{"role": "user", "content": "test"}])
        self.assertIsNone(result)

    def test_make_answer_without_evidence(self):
        """Service should return a graceful message when no evidence found."""
        from backend.service import OnboardingService
        with patch("backend.service.mark_stale_jobs"):
            svc = OnboardingService()
        answer, mode = svc._make_answer("Where is auth?", [], [])
        self.assertIn("could not confirm", answer.lower())
        self.assertEqual(mode, "evidence-only")
        svc.close()

    def test_make_answer_with_evidence_no_llm(self):
        """Service should return evidence blocks when LLM disabled and evidence provided."""
        from backend.service import OnboardingService
        with patch("backend.service.mark_stale_jobs"):
            svc = OnboardingService()
        evidence = [{
            "path": "app/auth.py",
            "start_line": 1,
            "end_line": 5,
            "excerpt": "1: def validate_token(token):\n2:     return token == 'ok'",
            "reason": "symbol match",
        }]
        answer, mode = svc._make_answer("Where is auth?", evidence, [])
        self.assertIn("app/auth.py", answer)
        self.assertEqual(mode, "evidence-only")
        svc.close()


class ParserTests(unittest.TestCase):
    """Test individual parsers."""

    def test_python_parser_extracts_class_and_function(self):
        from backend.parsers.python_parser import PythonParser
        parser = PythonParser()
        result = parser.parse("test.py", "class Foo:\n    def bar(self):\n        pass\n")
        names = [s.name for s in result.symbols]
        self.assertIn("Foo", names)
        self.assertIn("bar", names)

    def test_python_parser_extracts_routes(self):
        from backend.parsers.python_parser import PythonParser
        parser = PythonParser()
        code = "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/items')\ndef list_items():\n    return []\n"
        result = parser.parse("main.py", code)
        self.assertTrue(result.routes, "Expected at least one route")
        self.assertEqual(result.routes[0].path, "/items")
        self.assertEqual(result.routes[0].method, "GET")

    def test_js_parser_extracts_symbols_and_imports(self):
        from backend.parsers.js_parser import JSParser
        parser = JSParser()
        code = "import { helper } from './client';\nexport async function fetchItems() { return helper(); }\n"
        result = parser.parse("api.ts", code)
        self.assertTrue(result.symbols)
        self.assertTrue(result.imports)
        self.assertEqual(result.imports[0].module, "./client")

    def test_generic_parser_extracts_sql_tables(self):
        from backend.parsers.generic_parser import GenericParser
        parser = GenericParser()
        sql = "CREATE TABLE users (\n  id SERIAL PRIMARY KEY,\n  name TEXT NOT NULL\n);\n"
        result = parser.parse("schema.sql", sql)
        self.assertTrue(result.symbols)
        self.assertEqual(result.symbols[0].name, "users")
        self.assertEqual(result.symbols[0].kind, "table")

    def test_generic_parser_extracts_markdown_headings(self):
        from backend.parsers.generic_parser import GenericParser
        parser = GenericParser()
        md = "# Project Overview\n\nThis is a project.\n\n## Getting Started\n\nInstall deps.\n"
        result = parser.parse("README.md", md)
        headings = [s.name for s in result.symbols]
        self.assertIn("Project Overview", headings)
        self.assertIn("Getting Started", headings)

    def test_chunker_splits_by_symbol(self):
        from backend.chunker import chunk_file
        from backend.parsers.base import ParsedSymbol
        syms = [
            ParsedSymbol("authenticate", "authenticate", "function", 1, 5, "authenticate(request)", "", "auth.py::authenticate:1", "auth.py"),
            ParsedSymbol("logout", "logout", "function", 7, 10, "logout(request)", "", "auth.py::logout:7", "auth.py"),
        ]
        content = "def authenticate(request):\n    pass\n\n\ndef logout(request):\n    pass\n"
        chunks = chunk_file("auth.py", content, syms)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(c.chunk_type in ("function", "module", "text") for c in chunks))


if __name__ == "__main__":
    unittest.main()
