import json
import tempfile
import unittest
from pathlib import Path

from backend.analyzer import analyze_repository, generate_guide, redact_secrets


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "README.md").write_text("# Demo\nA small inventory service.\n", encoding="utf-8")
        (self.root / "app").mkdir()
        (self.root / "app" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "app" / "main.py").write_text(
            "from .service import compute\n"
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/items')\n"
            "def list_items():\n"
            "    return compute()\n"
            "API_TOKEN = 'ghp_123456789012345678901234567890'\n",
            encoding="utf-8",
        )
        (self.root / "app" / "service.py").write_text('def compute():\n    """Return inventory items."""\n    return []\n', encoding="utf-8")
        (self.root / "frontend").mkdir()
        (self.root / "frontend" / "api.ts").write_text("import { helper } from './client';\nexport async function fetchItems() { return helper(); }\n", encoding="utf-8")
        (self.root / "frontend" / "client.ts").write_text("export function helper() { return []; }\n", encoding="utf-8")
        (self.root / ".env.example").write_text("API_TOKEN=example-secret\nPORT=8000\n", encoding="utf-8")
        (self.root / "package.json").write_text(json.dumps({"dependencies": {"react": "^19"}, "scripts": {"test": "vitest"}}), encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_main.py").write_text("def test_health(): pass\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "ignored.js").write_text("should not be scanned", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_extracts_symbols_imports_calls_and_api_routes(self):
        analysis = analyze_repository(self.root)
        self.assertIn("list_items", [symbol["name"] for symbol in analysis["symbols"]])
        self.assertIn("compute", [symbol["name"] for symbol in analysis["symbols"]])
        self.assertIn({"method": "GET", "path": "/items", "file_path": "app/main.py", "handler": "list_items", "line_number": 5}, analysis["routes"])
        self.assertTrue(any(edge["relation"] == "imports" and edge["source_key"] == "app/main.py" and edge["target_key"] == "app/service.py" for edge in analysis["relationships"]))
        self.assertTrue(any(edge["relation"] == "calls (name match)" and edge["target_key"].startswith("app/service.py::compute") for edge in analysis["relationships"]))
        self.assertTrue(any(edge["source_key"] == "frontend/api.ts" and edge["target_key"] == "frontend/client.ts" for edge in analysis["relationships"]))
        self.assertNotIn("node_modules/ignored.js", [item["path"] for item in analysis["files"]])

    def test_redacts_secrets_before_content_is_returned(self):
        analysis = analyze_repository(self.root)
        contents = {item["path"]: item["content"] for item in analysis["files"]}
        self.assertNotIn("example-secret", contents[".env.example"])
        self.assertNotIn("ghp_123456789012345678901234567890", contents["app/main.py"])
        json_secret = redact_secrets('{"client_secret": "sensitive-value"}')
        self.assertNotIn("sensitive-value", json_secret)
        self.assertIn("[REDACTED]", json_secret)
        bearer = redact_secrets("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", bearer)

    def test_guide_uses_detected_repository_evidence(self):
        analysis = analyze_repository(self.root)
        guide = generate_guide(analysis, {"owner": "octo", "name": "demo"})
        self.assertEqual(guide["overview"]["repository"], "octo/demo")
        self.assertIn("app/main.py", guide["entry_points"])
        self.assertEqual(guide["api_routes"][0]["path"], "/items")
        self.assertIn("react", [value.lower() for value in guide["dependencies"]])
        self.assertIn("import_graph_mermaid", guide["architecture"])


if __name__ == "__main__":
    unittest.main()
