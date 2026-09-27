"""Validate and fetch public GitHub repositories without executing their code."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from backend.config import settings


class RepositoryInputError(ValueError):
    pass


@dataclass(frozen=True)
class GitHubRepository:
    owner: str
    name: str
    url: str


def validate_github_url(value: str) -> GitHubRepository:
    if not isinstance(value, str) or len(value) > 500:
        raise RepositoryInputError("Enter a valid public GitHub repository URL.")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as exc:
        raise RepositoryInputError("The repository URL is malformed.") from exc
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port is not None:
        raise RepositoryInputError("Only HTTPS URLs hosted on github.com are supported.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RepositoryInputError("Credentials, query strings, and fragments are not allowed in repository URLs.")
    segments = [part for part in parsed.path.strip("/").split("/") if part]
    if len(segments) != 2:
        raise RepositoryInputError("Use a repository URL in the form https://github.com/owner/repository.")
    owner, name = segments
    if name.lower().endswith(".git"):
        name = name[:-4]
    safe_segment = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
    if not safe_segment.fullmatch(owner) or not safe_segment.fullmatch(name) or owner in {".", ".."} or name in {".", ".."}:
        raise RepositoryInputError("The owner or repository name contains unsupported characters.")
    return GitHubRepository(owner, name, f"https://github.com/{owner}/{name}.git")


def _force_remove_readonly(func, path, exc_info) -> None:  # noqa: ANN001
    """Error handler for shutil.rmtree that clears read-only bits on Windows."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _remove_generated_path(path: Path, parent: Path) -> None:
    parent_resolved = parent.resolve()
    candidate = path.resolve()
    if candidate == parent_resolved or not candidate.is_relative_to(parent_resolved):
        raise RuntimeError("Refusing to remove a path outside the repository data directory.")
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path, onexc=_force_remove_readonly)


def clone_repository(url: str, repository_id: str) -> tuple[Path, str | None]:
    repo = validate_github_url(url)
    base = settings.repositories_dir
    base.mkdir(parents=True, exist_ok=True)
    destination = base / repository_id
    staging = base / f"{repository_id}.staging"
    _remove_generated_path(destination, base)
    _remove_generated_path(staging, base)
    hooks_dir = Path(tempfile.mkdtemp(prefix="onboard-empty-hooks-"))
    env = os.environ.copy()
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_LFS_SKIP_SMUDGE": "1",
    })
    command = [
        "git", "-c", f"core.hooksPath={hooks_dir}", "clone", "--depth=1", "--single-branch",
        "--no-tags", "--no-recurse-submodules", "--filter=blob:limit=1048576", "--", repo.url, str(staging),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=settings.clone_timeout_seconds, env=env, check=False)
        if result.returncode != 0:
            detail = (result.stderr or "Git clone failed.").strip().splitlines()
            message = detail[-1][:400] if detail else "Git clone failed."
            raise RuntimeError(f"Repository download failed: {message}")
        branch_result = subprocess.run(
            ["git", "-C", str(staging), "-c", f"core.hooksPath={hooks_dir}", "branch", "--show-current"],
            capture_output=True, text=True, timeout=10, env=env, check=False,
        )
        branch = branch_result.stdout.strip() or None
        staging.replace(destination)
        return destination, branch
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Repository download exceeded {settings.clone_timeout_seconds} seconds.") from exc
    finally:
        _remove_generated_path(staging, base)
        _remove_generated_path(hooks_dir, hooks_dir.parent)
