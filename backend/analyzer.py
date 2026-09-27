"""Bounded, deterministic repository analysis. Repository files are data, never code to execute."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import posixpath
import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from backend.parsers.registry import parse_file


LOGGER = logging.getLogger("onboard.analyzer")


MAX_FILES = 12_000
MAX_DIRECTORIES = 20_000
MAX_FILE_CANDIDATES = 50_000
MAX_FILE_BYTES = 750_000
MAX_TOTAL_BYTES = 80_000_000
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "vendor", "dist", "build", "coverage", "target",
    ".venv", "venv", "__pycache__", ".next", ".nuxt", "out", "site-packages", "bower_components",
}
EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte", ".html", ".css",
    ".scss", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".sql",
    ".sh", ".bash", ".yml", ".yaml", ".toml", ".json", ".md", ".mdx", ".rst", ".txt", ".env", ".ini",
    ".cfg", ".xml", ".graphql", ".proto", ".tf", ".dockerfile", ".makefile",
}
SPECIAL_FILES = {
    "dockerfile", "makefile", "readme", "license", "procfile", "justfile", "requirements.txt",
    "pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "gemfile",
    "composer.json", "docker-compose.yml", "docker-compose.yaml", ".gitignore", ".env.example", ".env.sample",
}
SECRET_LINE = re.compile(
    r"(?i)([\"']?\b[a-z0-9_.-]*(?:password|passwd|secret|token|authorization|api[_-]?key|private[_-]?key|access[_-]?key|client[_-]?secret)"
    r"[a-z0-9_.-]*[\"']?\s*[:=]\s*)(Bearer\s+[A-Za-z0-9._~+/=-]{8,}|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^,\s#;}]+)"
)
URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@/\s]+(@)", re.I)
TOKEN_PATTERNS = [
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{15,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
]
IMPORT_FROM_RE = re.compile(r"^\s*(?:import\s+.+?\s+from\s+|export\s+.+?\s+from\s+|import\s*)['\"]([^'\"]+)['\"]", re.M)
REQUIRE_RE = re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)")
JS_SYMBOL_RE = re.compile(
    r"^\s*(?:(?:export|default|async)\s+)*(class|function|interface|type|enum)\s+([A-Za-z_$][\w$]*)|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^\n]*\)|[\w$]+)\s*=>",
    re.M,
)
JS_ROUTE_RE = re.compile(r"(?:\.(get|post|put|patch|delete|options|head)\s*\(\s*|@(app|router)\.(get|post|put|patch|delete)\s*\(\s*)['\"]([^'\"]+)['\"]", re.I)
PY_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "route"}


def redact_secrets(text: str, path: str = "") -> str:
    """Mask likely secret values before content is persisted or shown to users."""
    if PurePosixPath(path).name.lower().startswith(".env"):
        lines = []
        for line in text.splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _value = line.split("=", 1)
                lines.append(f"{key}=[REDACTED]")
            else:
                lines.append(line)
        text = "\n".join(lines)
    text = SECRET_LINE.sub(r"\1[REDACTED]", text)
    text = URL_CREDENTIALS.sub(r"\1[REDACTED]\2", text)
    for pattern in TOKEN_PATTERNS:
        text = pattern.sub("[REDACTED_TOKEN]", text)
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]", text, flags=re.S)
    return text


def _classify(path: str) -> str:
    lower = path.lower()
    name = PurePosixPath(lower).name
    if name.startswith("readme") or lower.startswith("docs/") or "/docs/" in lower or PurePosixPath(lower).suffix in {".md", ".mdx", ".rst"}:
        return "documentation"
    if name.startswith(".env") or name in {"dockerfile", "makefile", "docker-compose.yml", "docker-compose.yaml", "pyproject.toml", "package.json", "requirements.txt", "go.mod", "cargo.toml"} or name.endswith((".yml", ".yaml", ".toml", ".ini", ".cfg")):
        return "configuration"
    if re.search(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|[._-])(test|spec)[._-]", lower):
        return "test"
    if PurePosixPath(lower).suffix in EXTENSIONS:
        return "source"
    return "other"


def _read_files(root: Path) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    total_bytes = 0
    directories_seen = 0
    candidates_seen = 0
    def log_walk_error(error: OSError) -> None:
        LOGGER.warning("directory skipped during scan error=%s", type(error).__name__)

    for current, dirs, filenames in os.walk(root, topdown=True, onerror=log_walk_error, followlinks=False):
        directories_seen += 1
        if directories_seen > MAX_DIRECTORIES:
            break
        dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS and not (Path(current) / d).is_symlink())
        for filename in sorted(filenames):
            candidates_seen += 1
            if candidates_seen > MAX_FILE_CANDIDATES:
                return collected
            full_path = Path(current) / filename
            if full_path.is_symlink() or not full_path.is_file():
                continue
            relative = full_path.relative_to(root).as_posix()
            basename = filename.lower()
            suffix = full_path.suffix.lower()
            if suffix not in EXTENSIONS and basename not in SPECIAL_FILES and not basename.startswith(".env"):
                continue
            try:
                size = full_path.stat().st_size
                if size > MAX_FILE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
                    continue
                raw = full_path.read_bytes()
            except OSError as exc:
                LOGGER.warning("file skipped path=%s error=%s", relative, type(exc).__name__)
                continue
            if b"\x00" in raw:
                continue
            decoded = raw.decode("utf-8", errors="replace")
            safe_content = redact_secrets(decoded, relative)
            collected.append({
                "path": relative,
                "kind": _classify(relative),
                "size": size,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "line_count": max(1, decoded.count("\n") + (0 if decoded.endswith("\n") else 1)),
                "content": safe_content,
            })
            total_bytes += size
            if len(collected) >= MAX_FILES:
                return collected
    return collected


def _python_symbols(path: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError):
        return symbols, imports, calls

    def visit_scope(nodes: list[ast.stmt], prefix: str = "") -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = node.name
                qualified = f"{prefix}.{name}" if prefix else name
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                signature = _function_signature(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
                symbols.append({
                    "file_path": path, "name": name, "qualified_name": qualified, "kind": kind,
                    "start_line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
                    "signature": signature, "docstring": ast.get_docstring(node) or "",
                    "symbol_key": f"{path}::{qualified}:{node.lineno}",
                })
                if isinstance(node, ast.ClassDef):
                    visit_scope(node.body, qualified)
                else:
                    for child in ast.walk(node):
                        if isinstance(child, ast.Call):
                            called = child.func.id if isinstance(child.func, ast.Name) else child.func.attr if isinstance(child.func, ast.Attribute) else ""
                            if called:
                                calls.append({"source_symbol": f"{path}::{qualified}:{node.lineno}", "name": called, "line_number": child.lineno})
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"module": alias.name, "line_number": node.lineno, "evidence": f"import {alias.name}"})
            elif isinstance(node, ast.ImportFrom):
                module = ("." * node.level) + (node.module or "")
                imports.append({"module": module, "line_number": node.lineno, "evidence": f"from {module} import ..."})
    visit_scope(tree.body)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                target = decorator.func
                method = target.attr.lower() if isinstance(target, ast.Attribute) else "route"
                if method not in PY_ROUTE_METHODS:
                    continue
                route = next((arg.value for arg in decorator.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)), None)
                if route is None:
                    continue
                methods = [method.upper()]
                if method == "route":
                    methods = [keyword.value.elts[0].value.upper() for keyword in decorator.keywords if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)) for _ in [0] if keyword.value.elts and isinstance(keyword.value.elts[0], ast.Constant)] or ["GET"]
                for http_method in methods:
                    imports.append({"route": route, "method": http_method, "handler": node.name, "line_number": node.lineno})
    return symbols, imports, calls


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults_offset = len(positional) - len(args.defaults)
    parts: list[str] = []
    for index, argument in enumerate(positional):
        text = ast.unparse(argument)
        if index >= defaults_offset:
            text += "=" + ast.unparse(args.defaults[index - defaults_offset])
        parts.append(text)
        if args.posonlyargs and index + 1 == len(args.posonlyargs):
            parts.append("/")
    if args.vararg:
        parts.append("*" + ast.unparse(args.vararg))
    elif args.kwonlyargs:
        parts.append("*")
    for index, argument in enumerate(args.kwonlyargs):
        text = ast.unparse(argument)
        default = args.kw_defaults[index]
        if default is not None:
            text += "=" + ast.unparse(default)
        parts.append(text)
    if args.kwarg:
        parts.append("**" + ast.unparse(args.kwarg))
    return f"{node.name}({', '.join(parts)})"


def _module_key(path: str) -> str:
    pure = PurePosixPath(path)
    no_suffix = str(pure.with_suffix(""))
    if no_suffix.endswith("/__init__"):
        no_suffix = no_suffix[: -len("/__init__")]
    return no_suffix.replace("/", ".")


def _resolve_python_import(source_path: str, module: str, paths_by_module: dict[str, str]) -> str | None:
    if module.startswith("."):
        parent = _module_key(source_path).split(".")[:-1]
        dots = len(module) - len(module.lstrip("."))
        base = parent[: max(0, len(parent) - dots + 1)]
        suffix = module[dots:].lstrip(".")
        absolute = ".".join(base + ([suffix] if suffix else []))
    else:
        absolute = module
    for candidate in [absolute, *[".".join(absolute.split(".")[:index]) for index in range(len(absolute.split(".")) - 1, 0, -1)]]:
        if candidate in paths_by_module:
            return paths_by_module[candidate]
    return None


def _resolve_js_import(source_path: str, module: str, known_paths: set[str]) -> str | None:
    if not module.startswith("."):
        return None
    source_parent = PurePosixPath(source_path).parent
    joined = PurePosixPath(posixpath.normpath(str(source_parent / module))).as_posix()
    candidates = [joined, *(joined + extension for extension in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue")), *(joined + "/index" + extension for extension in (".js", ".jsx", ".ts", ".tsx"))]
    return next((candidate for candidate in candidates if candidate in known_paths and not candidate.startswith("../")), None)


def analyze_repository(root: Path) -> dict[str, Any]:
    files = _read_files(root)
    paths = {entry["path"] for entry in files}
    by_module: dict[str, str] = {}
    for entry in files:
        if entry["path"].endswith(".py"):
            by_module[_module_key(entry["path"])] = entry["path"]
    symbols: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    call_candidates: list[dict[str, Any]] = []
    external_imports: set[str] = set()
    for entry in files:
        path, content = entry["path"], entry["content"]
        suffix = PurePosixPath(path).suffix.lower()
        parsed = parse_file(path, content)
        extracted: list[dict[str, Any]] = [
            {
                "file_path": sym.file_path, "name": sym.name, "qualified_name": sym.qualified_name,
                "kind": sym.kind, "start_line": sym.start_line, "end_line": sym.end_line,
                "signature": sym.signature, "docstring": sym.docstring, "symbol_key": sym.symbol_key,
            }
            for sym in parsed.symbols
        ]
        imports: list[dict[str, Any]] = [
            {"module": imp.module, "line_number": imp.line_number, "evidence": imp.evidence}
            for imp in parsed.imports
        ]
        calls: list[dict[str, Any]] = parsed.calls
        for route in parsed.routes:
            routes.append({"method": route.method, "path": route.path, "file_path": path, "handler": route.handler, "line_number": route.line_number})
        symbols.extend(extracted)
        call_candidates.extend(calls)
        for symbol in extracted:
            relations.append({"source_type": "file", "source_key": path, "target_type": "symbol", "target_key": symbol["symbol_key"], "relation": "defines", "evidence": symbol["signature"], "line_number": symbol["start_line"]})
        for item in imports:
            if "route" in item:
                routes.append({"method": item["method"], "path": item["route"], "file_path": path, "handler": item["handler"], "line_number": item["line_number"]})
                continue
            module = item["module"]
            if suffix in {".py", ".pyi"}:
                target = _resolve_python_import(path, module, by_module)
            else:
                target = _resolve_js_import(path, module, paths)
            if target:
                relations.append({"source_type": "file", "source_key": path, "target_type": "file", "target_key": target, "relation": "imports", "evidence": item["evidence"], "line_number": item["line_number"]})
            elif not module.startswith(".") and not module.startswith("/"):
                external_imports.add(module.split(".")[0])
    counts_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol in symbols:
        if symbol["kind"] in {"function", "asyncfunction"}:
            counts_by_name[symbol["name"]].append(symbol)
    for call in call_candidates:
        candidates = counts_by_name.get(call["name"], [])
        if len(candidates) == 1:
            target = candidates[0]
            relations.append({
                "source_type": "symbol", "source_key": call["source_symbol"], "target_type": "symbol",
                "target_key": target["symbol_key"], "relation": "calls (name match)",
                "evidence": f"Call to {call['name']} resolved by a unique repository symbol name.", "line_number": call["line_number"],
            })
    for route in routes:
        entry_path = route["file_path"]
        relations.append({
            "source_type": "file", "source_key": entry_path, "target_type": "api", "target_key": f"{route['method']} {route['path']}",
            "relation": "defines route", "evidence": route["handler"], "line_number": route["line_number"],
        })
    return {
        "files": files, "symbols": symbols, "relationships": relations, "routes": routes,
        "external_imports": sorted(external_imports),
        "counts": {"files": len(files), "symbols": len(symbols), "relationships": len(relations), "routes": len(routes)},
        "file_kinds": dict(Counter(item["kind"] for item in files)),
        "limits": {"maximum_files": MAX_FILES, "maximum_file_bytes": MAX_FILE_BYTES, "maximum_total_bytes": MAX_TOTAL_BYTES},
    }


def _top_level_directories(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for entry in files:
        parts = entry["path"].split("/")
        key = parts[0] if len(parts) > 1 else "."
        buckets[key].append(entry["path"])
    return [{"path": key, "file_count": len(paths), "examples": paths[:6]} for key, paths in sorted(buckets.items(), key=lambda row: (-len(row[1]), row[0]))]


def _dependencies_from_manifests(files: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for entry in files:
        path, content = entry["path"], entry["content"]
        name = PurePosixPath(path).name.lower()
        if name == "package.json":
            try:
                manifest = json.loads(content)
                found.update(manifest.get("dependencies", {}).keys())
                found.update(manifest.get("devDependencies", {}).keys())
            except (ValueError, AttributeError):
                continue
        elif name == "requirements.txt":
            for line in content.splitlines():
                match = re.match(r"\s*([A-Za-z0-9_.-]+)", line)
                if match and not line.lstrip().startswith(("#", "-")):
                    found.add(match.group(1))
        elif name in {"pyproject.toml", "cargo.toml"}:
            try:
                manifest = tomllib.loads(content)
                if name == "pyproject.toml":
                    project = manifest.get("project", {})
                    requirements = list(project.get("dependencies", []))
                    requirements.extend(requirement for group in project.get("optional-dependencies", {}).values() for requirement in group)
                    for requirement in requirements:
                        match = re.match(r"\s*([A-Za-z0-9_.-]+)", requirement)
                        if match:
                            found.add(match.group(1))
                else:
                    found.update(manifest.get("dependencies", {}).keys())
            except (tomllib.TOMLDecodeError, AttributeError, TypeError):
                continue
        elif name == "go.mod":
            found.update(re.findall(r"^\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)\s+v[\w.+-]+", content, re.M))
    return sorted(found)[:100]


def generate_guide(analysis: dict[str, Any], repository: dict[str, Any]) -> dict[str, Any]:
    files = analysis["files"]
    readme = next((item for item in files if PurePosixPath(item["path"]).name.lower().startswith("readme")), None)
    readme_excerpt = "\n".join(readme["content"].splitlines()[:30]) if readme else "Could not confirm a project description: no README was found."
    paths = {item["path"] for item in files}
    entry_names = {"main.py", "app.py", "wsgi.py", "asgi.py", "manage.py", "server.js", "index.js", "index.ts", "main.ts", "main.tsx", "src/main.ts", "src/main.tsx", "src/index.tsx"}
    entry_points = sorted(path for path in paths if PurePosixPath(path).name.lower() in entry_names or path.lower() in entry_names)
    env_names: set[str] = set()
    for item in files:
        content = item["content"]
        env_names.update(re.findall(r"\b(?:process\.env\.|import\.meta\.env\.)\s*([A-Z][A-Z0-9_]{2,})", content))
        env_names.update(re.findall(r"\bos\.(?:getenv|environ\.get)\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]", content))
        env_names.update(re.findall(r"\bgetenv\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]", content))
        env_names.update(re.findall(r"\$\{?([A-Z][A-Z0-9_]{2,})", content))
        if PurePosixPath(item["path"]).name.lower().startswith(".env"):
            env_names.update(line.split("=", 1)[0].strip() for line in content.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    env_names = {name for name in env_names if name and len(name) < 80}
    api_routes = analysis["routes"]
    dependencies = _dependencies_from_manifests(files)
    frameworks = []
    combined_paths = "\n".join(paths).lower()
    dependency_text = " ".join([*analysis["external_imports"], *dependencies]).lower()
    for marker, label in [("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"), ("express", "Express"), ("next", "Next.js"), ("react", "React"), ("vue", "Vue"), ("spring", "Spring")]:
        if marker in combined_paths or marker in dependency_text:
            frameworks.append(label)
    stack = sorted(set(frameworks))
    technology_files = sorted(path for path in paths if PurePosixPath(path).name.lower() in {"package.json", "pyproject.toml", "requirements.txt", "go.mod", "cargo.toml", "pom.xml", "build.gradle"})
    commands = []
    for item in files:
        if PurePosixPath(item["path"]).name.lower() == "package.json":
            try:
                scripts = json.loads(item["content"]).get("scripts", {})
                commands.extend(f"npm run {name}" for name in scripts if name in {"dev", "start", "test", "build", "lint"})
            except (ValueError, AttributeError):
                pass
    test_files = [item["path"] for item in files if item["kind"] == "test"]
    python_files = [item for item in files if item["path"].endswith((".py", ".pyi"))]
    if python_files:
        commands.append("python -m venv .venv")
        if "requirements.txt" in paths:
            commands.append("python -m pip install -r requirements.txt")
        if any("pytest" in dependency.lower() for dependency in dependencies):
            commands.append("python -m pytest")
        elif any("unittest" in item["content"] or "TestCase" in item["content"] for item in python_files):
            commands.append("python -m unittest discover -s tests")
    relationship_lines = []
    import_edges = [edge for edge in analysis["relationships"] if edge["relation"] == "imports"]
    for edge in import_edges[:40]:
        relationship_lines.append(f'  "{edge["source_key"]}" --> "{edge["target_key"]}"')
    diagram = "graph LR\n" + "\n".join(relationship_lines) if relationship_lines else "No local import edges were confidently resolved from the supported syntax."
    overview = {
        "repository": f"{repository['owner']}/{repository['name']}",
        "description": readme_excerpt[:4000],
        "technology_stack": stack or ["Could not confirm from the scanned manifests and imports."],
        "manifest_files": technology_files,
        "counts": analysis["counts"],
    }
    return {
        "overview": overview,
        "structure": _top_level_directories(files),
        "architecture": {"import_graph_mermaid": diagram, "resolved_import_edges": len(import_edges), "api_route_count": len(api_routes)},
        "entry_points": entry_points,
        "api_routes": api_routes[:250],
        "external_imports": analysis["external_imports"][:100],
        "dependencies": dependencies,
        "environment_variables": sorted(env_names),
        "setup_and_test_commands": list(dict.fromkeys(commands)),
        "test_files": test_files[:200],
        "deployment_files": sorted(path for path in paths if PurePosixPath(path).name.lower().startswith(("docker", "compose")) or path.startswith(".github/workflows/")),
        "features": [],
        "limitations": [
            "Feature summaries and business-flow descriptions are not inferred unless supported by extracted routes, imports, symbols, and documentation.",
            "Only Python AST and conservative JavaScript/TypeScript text extraction are implemented; other languages are classified but not deeply parsed.",
            "Environment variable names are collected from text and may include false positives. Values are redacted.",
        ],
    }


def create_chunks(files: list[dict], symbols: list[dict]) -> list[dict]:
    """Create semantic chunks from analyzed files and symbols, returning dicts for DB storage."""
    from backend.chunker import chunk_file, Chunk
    from backend.parsers.base import ParsedSymbol

    # Build symbol lookup by file path
    symbols_by_path: dict[str, list[ParsedSymbol]] = {}
    for sym in symbols:
        path = sym["file_path"]
        parsed_sym = ParsedSymbol(
            name=sym["name"],
            qualified_name=sym["qualified_name"],
            kind=sym["kind"],
            start_line=sym["start_line"],
            end_line=sym["end_line"],
            signature=sym.get("signature", ""),
            docstring=sym.get("docstring", ""),
            symbol_key=sym.get("symbol_key", ""),
            file_path=path,
        )
        symbols_by_path.setdefault(path, []).append(parsed_sym)

    all_chunks: list[dict] = []
    for entry in files:
        path = entry["path"]
        content = entry["content"]
        kind = entry.get("kind", "source")
        file_syms = symbols_by_path.get(path, [])
        try:
            file_chunks = chunk_file(path, content, file_syms, kind)
        except Exception:
            continue
        for chunk in file_chunks:
            all_chunks.append({
                "file_path": path,
                "chunk_text": chunk.chunk_text,
                "chunk_type": chunk.chunk_type,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "language": chunk.language,
                "symbol_name": chunk.symbol_name,
                "extra_meta": chunk.extra_meta,
            })
    return all_chunks
