import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config
from backend.service import OnboardingService


class RepositoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = config.settings.data_dir
        object.__setattr__(config.settings, "data_dir", Path(self.temp.name) / "data")
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
        (self.fixture / "src" / "service.py").write_text("def get_user(user_id):\n    return {'id': user_id}\n", encoding="utf-8")
        self.service = OnboardingService()

    def tearDown(self):
        self.service.close()
        object.__setattr__(config.settings, "data_dir", self.old_data_dir)
        self.temp.cleanup()

    def test_repository_job_persists_analysis_and_supports_chat(self):
        with patch("backend.service.clone_repository", return_value=(self.fixture, "main")):
            queued = self.service.register_repository("https://github.com/octo/fixture")
            deadline = time.monotonic() + 5
            repository = self.service.repository(queued["repository_id"])
            while repository["status"] in {"queued", "processing"} and time.monotonic() < deadline:
                time.sleep(0.02)
                repository = self.service.repository(queued["repository_id"])
        self.assertEqual(repository["status"], "completed", repository.get("error"))
        overview = self.service.overview(queued["repository_id"])
        self.assertGreaterEqual(overview["counts"]["symbols"], 2)
        detail = self.service.file_detail(queued["repository_id"], "src/main.py")
        self.assertNotIn("demo-secret-value", detail["content"])
        self.assertTrue(self.service.routes(queued["repository_id"]))
        self.assertIn("src/main.py", self.service.guide(queued["repository_id"])["entry_points"])
        answer = self.service.chat(queued["repository_id"], "Where is the user route implemented?")
        self.assertTrue(answer["evidence"])
        self.assertEqual(len(self.service.conversations(queued["repository_id"])), 1)
        followup = self.service.chat(queued["repository_id"], "What does that function call?", answer["conversation_id"])
        self.assertEqual(followup["conversation_id"], answer["conversation_id"])
        with self.assertRaises(KeyError):
            self.service.list_files("missing-repository")


if __name__ == "__main__":
    unittest.main()
