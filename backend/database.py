"""SQLite persistence. Connections are short-lived and safe across worker threads."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.config import settings


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS repositories (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  name TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  default_branch TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  files_scanned INTEGER NOT NULL DEFAULT 0,
  symbols_extracted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_repo_created ON jobs(repository_id, created_at DESC);
CREATE TABLE IF NOT EXISTS files (
  repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  kind TEXT NOT NULL,
  size INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  line_count INTEGER NOT NULL,
  content TEXT NOT NULL,
  PRIMARY KEY(repository_id, path)
);
CREATE INDEX IF NOT EXISTS files_repo_kind ON files(repository_id, kind);
CREATE TABLE IF NOT EXISTS symbols (
  repository_id TEXT NOT NULL,
  symbol_key TEXT NOT NULL,
  file_path TEXT NOT NULL,
  name TEXT NOT NULL,
  qualified_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL,
  signature TEXT NOT NULL,
  docstring TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(repository_id, symbol_key),
  FOREIGN KEY(repository_id, file_path) REFERENCES files(repository_id, path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS symbols_repo_name ON symbols(repository_id, name);
CREATE INDEX IF NOT EXISTS symbols_repo_file ON symbols(repository_id, file_path);
CREATE TABLE IF NOT EXISTS relationships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_key TEXT NOT NULL,
  target_type TEXT NOT NULL,
  target_key TEXT NOT NULL,
  relation TEXT NOT NULL,
  evidence TEXT NOT NULL,
  line_number INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS relationships_source ON relationships(repository_id, source_type, source_key);
CREATE INDEX IF NOT EXISTS relationships_target ON relationships(repository_id, target_type, target_key);
CREATE TABLE IF NOT EXISTS api_routes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  method TEXT NOT NULL,
  path TEXT NOT NULL,
  file_path TEXT NOT NULL,
  handler TEXT NOT NULL,
  line_number INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS routes_repo_path ON api_routes(repository_id, path);
CREATE TABLE IF NOT EXISTS guides (
  repository_id TEXT PRIMARY KEY REFERENCES repositories(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  repository_id TEXT NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  evidence TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id, id);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS file_search USING fts5(repository_id UNINDEXED, path, content, tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS files_search_insert AFTER INSERT ON files BEGIN
  INSERT INTO file_search(repository_id,path,content) VALUES(new.repository_id,new.path,new.content);
END;
CREATE TRIGGER IF NOT EXISTS files_search_delete AFTER DELETE ON files BEGIN
  DELETE FROM file_search WHERE repository_id=old.repository_id AND path=old.path;
END;
"""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = path or settings.database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(target, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 20000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize(path: Path | None = None) -> None:
    target = path or settings.database_path
    with connect(target) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        try:
            connection.executescript(FTS_SCHEMA)
            if connection.execute("SELECT 1 FROM file_search LIMIT 1").fetchone() is None:
                connection.execute("INSERT INTO file_search(repository_id,path,content) SELECT repository_id,path,content FROM files")
        except sqlite3.OperationalError:
            # The service retains a bounded LIKE-search fallback when SQLite omits FTS5.
            pass
        # A process crash must not leave jobs looking active forever.
        connection.execute(
            "UPDATE jobs SET status='failed', finished_at=datetime('now'), error='Server restarted while job was active' WHERE status IN ('queued','processing')"
        )
        connection.execute(
            "UPDATE repositories SET status='failed', updated_at=datetime('now'), error='Server restarted while job was active' WHERE status IN ('queued','processing')"
        )


def fts_available(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='file_search'").fetchone() is not None
