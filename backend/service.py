"""Application services: repository lifecycle, analysis jobs, guides, search, and chat."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from backend.analyzer import analyze_repository, generate_guide
from backend.config import settings
from backend.database import connect, initialize
from backend.github import RepositoryInputError, clone_repository, validate_github_url
from backend.retrieval import OpenAICompatibleClient, make_evidence_answer, search_repository


LOGGER = logging.getLogger("onboard.service")
DEICTIC_RE = re.compile(r"\b(that|this|above|previous|it|there|those)\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _repo_dict(row: Any) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class OnboardingService:
    def __init__(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.repositories_dir.mkdir(parents=True, exist_ok=True)
        initialize()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="repository-analysis")
        self._submit_lock = threading.Lock()
        self._queue_slots = threading.BoundedSemaphore(6)
        self.llm = OpenAICompatibleClient()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def validate_repository(self, url: str) -> dict[str, Any]:
        repo = validate_github_url(url)
        return {"valid": True, "owner": repo.owner, "name": repo.name, "canonical_url": repo.url, "public_only": True}

    def register_repository(self, url: str) -> dict[str, Any]:
        repo = validate_github_url(url)
        now, job_id = utc_now(), str(uuid.uuid4())
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM repositories WHERE url=?", (repo.url,)).fetchone()
            if existing:
                repository_id = existing["id"]
                active_job = db.execute("SELECT id,status FROM jobs WHERE repository_id=? AND status IN ('queued','processing') ORDER BY created_at DESC LIMIT 1", (repository_id,)).fetchone()
                if active_job:
                    return {"repository_id": repository_id, "job_id": active_job["id"], "status": active_job["status"], "owner": repo.owner, "name": repo.name}
                db.execute("UPDATE repositories SET status='queued', updated_at=?, error=NULL WHERE id=?", (now, repository_id))
            else:
                repository_id = str(uuid.uuid4())
                db.execute(
                    "INSERT INTO repositories(id,owner,name,url,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (repository_id, repo.owner, repo.name, repo.url, "queued", now, now),
                )
            db.execute("INSERT INTO jobs(id,repository_id,status,created_at) VALUES(?,?,?,?)", (job_id, repository_id, "queued", now))
        scheduled = self._schedule(job_id, repository_id, repo.url)
        if not scheduled:
            error = "The analysis queue is full. Try again after another repository finishes."
            self._reject_queued_job(job_id, repository_id, error)
            return {"repository_id": repository_id, "job_id": job_id, "status": "failed", "error": error, "owner": repo.owner, "name": repo.name}
        return {"repository_id": repository_id, "job_id": job_id, "status": "queued", "owner": repo.owner, "name": repo.name}

    def reanalyze(self, repository_id: str) -> dict[str, Any]:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone()
            if not row:
                raise KeyError("Repository not found.")
            active_job = db.execute("SELECT id,status FROM jobs WHERE repository_id=? AND status IN ('queued','processing') ORDER BY created_at DESC LIMIT 1", (repository_id,)).fetchone()
            if active_job:
                return {"repository_id": repository_id, "job_id": active_job["id"], "status": active_job["status"]}
            job_id, now = str(uuid.uuid4()), utc_now()
            db.execute("UPDATE repositories SET status='queued',updated_at=?,error=NULL WHERE id=?", (now, repository_id))
            db.execute("INSERT INTO jobs(id,repository_id,status,created_at) VALUES(?,?,?,?)", (job_id, repository_id, "queued", now))
        if not self._schedule(job_id, repository_id, row["url"]):
            error = "The analysis queue is full. Try again after another repository finishes."
            self._reject_queued_job(job_id, repository_id, error)
            return {"repository_id": repository_id, "job_id": job_id, "status": "failed", "error": error}
        return {"repository_id": repository_id, "job_id": job_id, "status": "queued"}

    def _schedule(self, job_id: str, repository_id: str, url: str) -> bool:
        with self._submit_lock:
            if not self._queue_slots.acquire(blocking=False):
                return False
            try:
                future = self._executor.submit(self._run_job, job_id, repository_id, url)
                future.add_done_callback(lambda _future: self._queue_slots.release())
                return True
            except RuntimeError:
                self._queue_slots.release()
                return False

    def _reject_queued_job(self, job_id: str, repository_id: str, error: str) -> None:
        now = utc_now()
        with connect() as db:
            db.execute("UPDATE jobs SET status='failed',finished_at=?,error=? WHERE id=?", (now, error, job_id))
            db.execute("UPDATE repositories SET status='failed',updated_at=?,error=? WHERE id=?", (now, error, repository_id))

    def _ensure_repository(self, repository_id: str) -> None:
        with connect() as db:
            if not db.execute("SELECT 1 FROM repositories WHERE id=?", (repository_id,)).fetchone():
                raise KeyError("Repository not found.")

    def _run_job(self, job_id: str, repository_id: str, url: str) -> None:
        started = utc_now()
        with connect() as db:
            db.execute("UPDATE jobs SET status='processing',started_at=? WHERE id=?", (started, job_id))
            db.execute("UPDATE repositories SET status='processing',updated_at=?,error=NULL WHERE id=?", (started, repository_id))
        try:
            clone_path, branch = clone_repository(url, repository_id)
            analysis = analyze_repository(clone_path)
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM files WHERE repository_id=?", (repository_id,))
                db.execute("DELETE FROM relationships WHERE repository_id=?", (repository_id,))
                db.execute("DELETE FROM api_routes WHERE repository_id=?", (repository_id,))
                db.executemany(
                    "INSERT INTO files(repository_id,path,kind,size,sha256,line_count,content) VALUES(?,?,?,?,?,?,?)",
                    [(repository_id, item["path"], item["kind"], item["size"], item["sha256"], item["line_count"], item["content"]) for item in analysis["files"]],
                )
                db.executemany(
                    "INSERT INTO symbols(repository_id,symbol_key,file_path,name,qualified_name,kind,start_line,end_line,signature,docstring) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [(repository_id, item["symbol_key"], item["file_path"], item["name"], item["qualified_name"], item["kind"], item["start_line"], item["end_line"], item["signature"], item["docstring"]) for item in analysis["symbols"]],
                )
                db.executemany(
                    "INSERT INTO relationships(repository_id,source_type,source_key,target_type,target_key,relation,evidence,line_number) VALUES(?,?,?,?,?,?,?,?)",
                    [(repository_id, edge["source_type"], edge["source_key"], edge["target_type"], edge["target_key"], edge["relation"], edge["evidence"], edge["line_number"]) for edge in analysis["relationships"]],
                )
                db.executemany(
                    "INSERT INTO api_routes(repository_id,method,path,file_path,handler,line_number) VALUES(?,?,?,?,?,?)",
                    [(repository_id, route["method"], route["path"], route["file_path"], route["handler"], route["line_number"]) for route in analysis["routes"]],
                )
                repo_row = db.execute("SELECT id,owner,name,url FROM repositories WHERE id=?", (repository_id,)).fetchone()
                guide = generate_guide(analysis, _repo_dict(repo_row))
                db.execute(
                    "INSERT INTO guides(repository_id,content,generated_at) VALUES(?,?,?) ON CONFLICT(repository_id) DO UPDATE SET content=excluded.content,generated_at=excluded.generated_at",
                    (repository_id, json.dumps(guide, ensure_ascii=False), utc_now()),
                )
                now = utc_now()
                db.execute("UPDATE repositories SET status='completed',default_branch=?,updated_at=?,error=NULL WHERE id=?", (branch, now, repository_id))
                db.execute("UPDATE jobs SET status='completed',finished_at=?,files_scanned=?,symbols_extracted=?,error=NULL WHERE id=?", (now, analysis["counts"]["files"], analysis["counts"]["symbols"], job_id))
            LOGGER.info("analysis completed repository_id=%s files=%d symbols=%d edges=%d", repository_id, analysis["counts"]["files"], analysis["counts"]["symbols"], analysis["counts"]["relationships"])
        except Exception as exc:
            message = str(exc)[:600] or type(exc).__name__
            LOGGER.exception("analysis failed repository_id=%s job_id=%s", repository_id, job_id)
            now = utc_now()
            with connect() as db:
                db.execute("UPDATE jobs SET status='failed',finished_at=?,error=? WHERE id=?", (now, message, job_id))
                db.execute("UPDATE repositories SET status='failed',updated_at=?,error=? WHERE id=?", (now, message, repository_id))

    def list_repositories(self) -> list[dict[str, Any]]:
        with connect() as db:
            rows = db.execute(
                "SELECT r.*, (SELECT id FROM jobs j WHERE j.repository_id=r.id ORDER BY j.created_at DESC LIMIT 1) AS latest_job_id, (SELECT COUNT(*) FROM files f WHERE f.repository_id=r.id) AS file_count FROM repositories r ORDER BY r.updated_at DESC"
            ).fetchall()
            return [_repo_dict(row) for row in rows]

    def repository(self, repository_id: str) -> dict[str, Any]:
        with connect() as db:
            row = db.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone()
            if not row:
                raise KeyError("Repository not found.")
            result = _repo_dict(row)
            job = db.execute("SELECT * FROM jobs WHERE repository_id=? ORDER BY created_at DESC LIMIT 1", (repository_id,)).fetchone()
            result["latest_job"] = _repo_dict(job) if job else None
            return result

    def overview(self, repository_id: str) -> dict[str, Any]:
        repository = self.repository(repository_id)
        with connect() as db:
            counts = {
                "files": db.execute("SELECT COUNT(*) FROM files WHERE repository_id=?", (repository_id,)).fetchone()[0],
                "symbols": db.execute("SELECT COUNT(*) FROM symbols WHERE repository_id=?", (repository_id,)).fetchone()[0],
                "relationships": db.execute("SELECT COUNT(*) FROM relationships WHERE repository_id=?", (repository_id,)).fetchone()[0],
                "api_routes": db.execute("SELECT COUNT(*) FROM api_routes WHERE repository_id=?", (repository_id,)).fetchone()[0],
            }
            language_rows = db.execute("SELECT path FROM files WHERE repository_id=? AND kind='source'", (repository_id,)).fetchall()
            language_counts: dict[str, int] = {}
            for row in language_rows:
                extension = PurePosixPath(row["path"]).suffix.lower().lstrip(".") or "unknown"
                language_counts[extension] = language_counts.get(extension, 0) + 1
            languages = sorted(language_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
            kinds = db.execute("SELECT kind,COUNT(*) AS count FROM files WHERE repository_id=? GROUP BY kind ORDER BY count DESC", (repository_id,)).fetchall()
            readme = db.execute("SELECT path,content FROM files WHERE repository_id=? AND lower(path) LIKE '%readme%' ORDER BY length(path) LIMIT 1", (repository_id,)).fetchone()
            top_symbols = db.execute("SELECT file_path,name,qualified_name,kind,start_line FROM symbols WHERE repository_id=? ORDER BY kind,name LIMIT 30", (repository_id,)).fetchall()
        return {
            "repository": repository, "counts": counts,
            "file_kinds": [{"kind": row["kind"], "count": row["count"]} for row in kinds],
            "languages": [{"extension": extension, "source_files": count} for extension, count in languages],
            "readme_excerpt": "\n".join(readme["content"].splitlines()[:20]) if readme else None,
            "important_symbols": [_repo_dict(row) for row in top_symbols], "limits": {"max_file_bytes": 750_000, "max_total_bytes": 80_000_000, "max_files": 12_000},
            "llm_enabled": self.llm.enabled,
        }

    def list_files(self, repository_id: str, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_repository(repository_id)
        with connect() as db:
            if query:
                rows = db.execute("SELECT path,kind,size,line_count,sha256 FROM files WHERE repository_id=? AND path LIKE ? ORDER BY path LIMIT ?", (repository_id, f"%{query[:150]}%", limit)).fetchall()
            else:
                rows = db.execute("SELECT path,kind,size,line_count,sha256 FROM files WHERE repository_id=? ORDER BY path LIMIT ?", (repository_id, limit)).fetchall()
            return [_repo_dict(row) for row in rows]

    def file_detail(self, repository_id: str, path: str) -> dict[str, Any]:
        normalized = PurePosixPath(path).as_posix()
        if normalized.startswith("../") or normalized.startswith("/") or "\\" in path:
            raise ValueError("Invalid repository-relative file path.")
        with connect() as db:
            row = db.execute("SELECT path,kind,size,sha256,line_count,content FROM files WHERE repository_id=? AND path=?", (repository_id, normalized)).fetchone()
            if not row:
                raise KeyError("File not found.")
            result = _repo_dict(row)
            result["symbols"] = [_repo_dict(symbol) for symbol in db.execute("SELECT name,qualified_name,kind,start_line,end_line,signature,docstring FROM symbols WHERE repository_id=? AND file_path=? ORDER BY start_line", (repository_id, normalized)).fetchall()]
            result["relationships"] = [_repo_dict(edge) for edge in db.execute("SELECT source_type,source_key,target_type,target_key,relation,evidence,line_number FROM relationships WHERE repository_id=? AND ((source_type='file' AND source_key=?) OR (target_type='file' AND target_key=?)) ORDER BY line_number LIMIT 150", (repository_id, normalized, normalized)).fetchall()]
            return result

    def symbols(self, repository_id: str, query: str = "", limit: int = 200) -> list[dict[str, Any]]:
        self._ensure_repository(repository_id)
        with connect() as db:
            if query:
                rows = db.execute("SELECT symbol_key,file_path,name,qualified_name,kind,start_line,end_line,signature,docstring FROM symbols WHERE repository_id=? AND (name LIKE ? OR qualified_name LIKE ? OR file_path LIKE ?) ORDER BY name LIMIT ?", (repository_id, f"%{query[:100]}%", f"%{query[:100]}%", f"%{query[:100]}%", limit)).fetchall()
            else:
                rows = db.execute("SELECT symbol_key,file_path,name,qualified_name,kind,start_line,end_line,signature,docstring FROM symbols WHERE repository_id=? ORDER BY file_path,start_line LIMIT ?", (repository_id, limit)).fetchall()
            return [_repo_dict(row) for row in rows]

    def relationships(self, repository_id: str, path: str = "", limit: int = 500) -> list[dict[str, Any]]:
        self._ensure_repository(repository_id)
        with connect() as db:
            if path:
                rows = db.execute("SELECT source_type,source_key,target_type,target_key,relation,evidence,line_number FROM relationships WHERE repository_id=? AND ((source_type='file' AND source_key=?) OR (target_type='file' AND target_key=?)) ORDER BY relation,line_number LIMIT ?", (repository_id, path, path, limit)).fetchall()
            else:
                rows = db.execute("SELECT source_type,source_key,target_type,target_key,relation,evidence,line_number FROM relationships WHERE repository_id=? ORDER BY relation,source_key LIMIT ?", (repository_id, limit)).fetchall()
            return [_repo_dict(row) for row in rows]

    def routes(self, repository_id: str, query: str = "", limit: int = 250) -> list[dict[str, Any]]:
        self._ensure_repository(repository_id)
        with connect() as db:
            rows = db.execute("SELECT method,path,file_path,handler,line_number FROM api_routes WHERE repository_id=? AND (?='' OR path LIKE ? OR file_path LIKE ?) ORDER BY path,method LIMIT ?", (repository_id, query, f"%{query[:120]}%", f"%{query[:120]}%", limit)).fetchall()
            return [_repo_dict(row) for row in rows]

    def guide(self, repository_id: str) -> dict[str, Any]:
        with connect() as db:
            row = db.execute("SELECT content,generated_at FROM guides WHERE repository_id=?", (repository_id,)).fetchone()
            if not row:
                repo = db.execute("SELECT id FROM repositories WHERE id=?", (repository_id,)).fetchone()
                if not repo:
                    raise KeyError("Repository not found.")
                raise RuntimeError("The onboarding guide is not ready until analysis completes.")
            return {"generated_at": row["generated_at"], **json.loads(row["content"])}

    def search(self, repository_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        self._ensure_repository(repository_id)
        return search_repository(repository_id, query, limit)

    def chat(self, repository_id: str, question: str, conversation_id: str | None = None) -> dict[str, Any]:
        if not isinstance(question, str) or not question.strip() or len(question) > 4_000:
            raise ValueError("Question must be between 1 and 4000 characters.")
        if conversation_id is not None and (not isinstance(conversation_id, str) or len(conversation_id) > 100):
            raise ValueError("Conversation ID is invalid.")
        with connect() as db:
            repo = db.execute("SELECT id,status FROM repositories WHERE id=?", (repository_id,)).fetchone()
            if not repo:
                raise KeyError("Repository not found.")
            if repo["status"] != "completed":
                raise RuntimeError("Repository analysis must complete before chat is available.")
            if conversation_id:
                conversation = db.execute("SELECT id FROM conversations WHERE id=? AND repository_id=?", (conversation_id, repository_id)).fetchone()
                if not conversation:
                    raise ValueError("Conversation does not belong to this repository.")
            else:
                conversation_id = str(uuid.uuid4())
                now = utc_now()
                db.execute("INSERT INTO conversations(id,repository_id,created_at,updated_at) VALUES(?,?,?,?)", (conversation_id, repository_id, now, now))
            previous = db.execute("SELECT role,content,evidence FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 12", (conversation_id,)).fetchall()
            history = [{"role": row["role"], "content": row["content"]} for row in reversed(previous)]
            previous_user = next((row["content"] for row in reversed(previous) if row["role"] == "user"), "")
        retrieval_query = question
        if previous_user and DEICTIC_RE.search(question):
            retrieval_query = f"{previous_user} {question}"
        evidence = self.search(repository_id, retrieval_query, 8)
        answer, mode = make_evidence_answer(question, evidence, self.llm, history)
        evidence_meta = [{"path": item["path"], "start_line": item["start_line"], "end_line": item["end_line"], "reason": item["reason"]} for item in evidence]
        now = utc_now()
        with connect() as db:
            db.execute("INSERT INTO messages(conversation_id,role,content,evidence,created_at) VALUES(?,?,?,?,?)", (conversation_id, "user", question, "[]", now))
            db.execute("INSERT INTO messages(conversation_id,role,content,evidence,created_at) VALUES(?,?,?,?,?)", (conversation_id, "assistant", answer, json.dumps(evidence_meta), utc_now()))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (utc_now(), conversation_id))
        return {"conversation_id": conversation_id, "answer": answer, "evidence": evidence_meta, "mode": mode}

    def conversation(self, repository_id: str, conversation_id: str) -> dict[str, Any]:
        with connect() as db:
            conversation = db.execute("SELECT id,repository_id,created_at,updated_at FROM conversations WHERE id=? AND repository_id=?", (conversation_id, repository_id)).fetchone()
            if not conversation:
                raise KeyError("Conversation not found.")
            messages = db.execute("SELECT role,content,evidence,created_at FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall()
            return {"conversation": _repo_dict(conversation), "messages": [{**_repo_dict(row), "evidence": json.loads(row["evidence"])} for row in messages]}

    def conversations(self, repository_id: str) -> list[dict[str, Any]]:
        with connect() as db:
            repo = db.execute("SELECT id FROM repositories WHERE id=?", (repository_id,)).fetchone()
            if not repo:
                raise KeyError("Repository not found.")
            rows = db.execute(
                "SELECT c.id,c.created_at,c.updated_at,(SELECT content FROM messages m WHERE m.conversation_id=c.id AND m.role='user' ORDER BY m.id LIMIT 1) AS preview FROM conversations c WHERE c.repository_id=? ORDER BY c.updated_at DESC LIMIT 50",
                (repository_id,),
            ).fetchall()
            return [_repo_dict(row) for row in rows]
