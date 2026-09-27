import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from backend.server import make_handler


class FakeLlm:
    enabled = False


class FakeService:
    llm = FakeLlm()

    def validate_repository(self, url):
        from backend.github import validate_github_url
        return validate_github_url(url)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(FakeService()))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_route(self):
        with urlopen(f"{self.base}/api/health") as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")

    def test_repository_validation_returns_a_client_error_for_bad_url(self):
        request = Request(f"{self.base}/api/repositories/validate", data=b'{"url":"http://example.com/repo"}', headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(HTTPError) as result:
            urlopen(request)
        self.assertEqual(result.exception.code, 400)
        self.assertIn("github.com", json.loads(result.exception.read())["error"])
        result.exception.close()


if __name__ == "__main__":
    unittest.main()
