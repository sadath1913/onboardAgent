"""Small same-origin JSON API and static asset server."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from backend.config import ROOT, settings
from backend.github import RepositoryInputError
from backend.service import OnboardingService


LOGGER = logging.getLogger("onboard.http")
MAX_BODY_BYTES = 256_000
STATIC_FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8")}


def make_handler(service: OnboardingService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "OnboardAgent/0.1"

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("client=%s message=%s", self.client_address[0], format % args)

        def _send(self, status: int, payload: object, content_type: str = "application/json; charset=utf-8") -> None:
            if content_type.startswith("application/json"):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            elif isinstance(payload, bytes):
                body = payload
            else:
                body = str(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ValueError("Invalid Content-Length header.")
            if length < 0 or length > MAX_BODY_BYTES:
                raise ValueError("Request body is too large.")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            try:
                result = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Request body must be valid JSON.") from exc
            if not isinstance(result, dict):
                raise ValueError("Request body must be a JSON object.")
            return result

        def _query(self, parsed) -> dict[str, str]:
            return {key: values[-1] for key, values in parse_qs(parsed.query, keep_blank_values=True).items()}

        def _dispatch(self, method: str) -> None:
            parsed = urlsplit(self.path)
            route = unquote(parsed.path)
            query = self._query(parsed)
            if route in STATIC_FILES and method == "GET":
                filename, content_type = STATIC_FILES[route]
                path = ROOT / "frontend" / filename
                self._send(200, path.read_bytes(), content_type)
                return
            if route == "/api/health" and method == "GET":
                self._send(200, {"status": "ok", "llm_enabled": service.llm.enabled})
                return
            if route == "/api/repositories/validate" and method == "POST":
                body = self._body()
                self._send(200, service.validate_repository(body.get("url", "")))
                return
            if route == "/api/repositories" and method == "GET":
                self._send(200, {"repositories": service.list_repositories()})
                return
            if route == "/api/repositories" and method == "POST":
                body = self._body()
                self._send(202, service.register_repository(body.get("url", "")))
                return
            parts = [part for part in route.split("/") if part]
            if len(parts) < 3 or parts[0] != "api" or parts[1] != "repositories":
                self._send(404, {"error": "Route not found."})
                return
            repository_id = parts[2]
            if len(parts) == 4 and parts[3] == "analyze" and method == "POST":
                self._send(202, service.reanalyze(repository_id))
                return
            if len(parts) == 4 and parts[3] in {"", "status"} and method == "GET":
                self._send(200, service.repository(repository_id))
                return
            if len(parts) == 3 and method == "GET":
                self._send(200, service.repository(repository_id))
                return
            if len(parts) == 4 and parts[3] == "overview" and method == "GET":
                self._send(200, service.overview(repository_id))
                return
            if len(parts) == 4 and parts[3] == "guide" and method == "GET":
                self._send(200, service.guide(repository_id))
                return
            if len(parts) == 4 and parts[3] == "files" and method == "GET":
                self._send(200, {"files": service.list_files(repository_id, query.get("query", ""), _bounded_int(query.get("limit"), 200, 1, 500))})
                return
            if len(parts) == 5 and parts[3] == "files" and parts[4] == "content" and method == "GET":
                self._send(200, service.file_detail(repository_id, query.get("path", "")))
                return
            if len(parts) == 4 and parts[3] == "symbols" and method == "GET":
                self._send(200, {"symbols": service.symbols(repository_id, query.get("query", ""), _bounded_int(query.get("limit"), 200, 1, 500))})
                return
            if len(parts) == 4 and parts[3] == "dependencies" and method == "GET":
                self._send(200, {"relationships": service.relationships(repository_id, query.get("path", ""), _bounded_int(query.get("limit"), 500, 1, 1000))})
                return
            if len(parts) == 4 and parts[3] == "apis" and method == "GET":
                self._send(200, {"routes": service.routes(repository_id, query.get("query", ""))})
                return
            if len(parts) == 4 and parts[3] == "search" and method == "GET":
                term = query.get("query", "")
                self._send(200, {"query": term, "results": service.search(repository_id, term, _bounded_int(query.get("limit"), 8, 1, 20))})
                return
            if len(parts) == 4 and parts[3] == "chat" and method == "POST":
                body = self._body()
                self._send(200, service.chat(repository_id, body.get("question", ""), body.get("conversation_id")))
                return
            if len(parts) == 4 and parts[3] == "conversations" and method == "GET":
                self._send(200, {"conversations": service.conversations(repository_id)})
                return
            if len(parts) == 5 and parts[3] == "conversations" and method == "GET":
                self._send(200, service.conversation(repository_id, parts[4]))
                return
            self._send(404, {"error": "Route not found."})

        def _handle(self, method: str) -> None:
            try:
                self._dispatch(method)
            except RepositoryInputError as exc:
                self._send(400, {"error": str(exc)})
            except ValueError as exc:
                self._send(400, {"error": str(exc)})
            except KeyError as exc:
                self._send(404, {"error": str(exc).strip("'")})
            except RuntimeError as exc:
                self._send(409, {"error": str(exc)})
            except BrokenPipeError:
                return
            except Exception:
                LOGGER.exception("request failed method=%s path=%s", method, self.path)
                self._send(500, {"error": "The request could not be completed."})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._send(405, {"error": "Method not allowed."})

        def do_DELETE(self) -> None:
            self._send(405, {"error": "Method not allowed."})

    return Handler


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        return max(minimum, min(maximum, int(value)))
    except ValueError:
        return default


def serve(service: OnboardingService | None = None) -> None:
    active_service = service or OnboardingService()
    server = ThreadingHTTPServer((settings.host, settings.port), make_handler(active_service))
    server.daemon_threads = True
    LOGGER.info("server listening host=%s port=%d", settings.host, settings.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("server stopping")
    finally:
        server.server_close()
        active_service.close()
