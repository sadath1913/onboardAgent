"""JavaScript/TypeScript regex-based parser."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from backend.parsers.base import BaseParser, ParseResult, ParsedSymbol, ParsedImport, ParsedRoute

IMPORT_FROM_RE = re.compile(
    r"^\s*(?:import\s+.+?\s+from\s+|export\s+.+?\s+from\s+|import\s*)['\"]([^'\"]+)['\"]",
    re.M,
)
REQUIRE_RE = re.compile(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)")
SYMBOL_RE = re.compile(
    r"^\s*(?:(?:export|default|async)\s+)*(class|function|interface|type|enum)\s+([A-Za-z_$][\w$]*)|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^\n]*\)|[\w$]+)\s*=>",
    re.M,
)
ROUTE_RE = re.compile(
    r"(?:\.(get|post|put|patch|delete|options|head)\s*\(\s*|"
    r"@(app|router)\.(get|post|put|patch|delete)\s*\(\s*)['\"]([^'\"]+)['\"]",
    re.I,
)
NEXT_PAGE_RE = re.compile(r"pages/([^.]+)\.(js|jsx|ts|tsx)$", re.I)
EXPRESS_ROUTER_RE = re.compile(r"(?:app|router)\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]", re.I)


class JSParser(BaseParser):
    language = "javascript"
    extensions = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte")

    def parse(self, path: str, content: str) -> ParseResult:
        suffix = PurePosixPath(path).suffix.lower()
        lang = "typescript" if suffix in (".ts", ".tsx") else "javascript"
        result = ParseResult(language=lang)

        for match in SYMBOL_RE.finditer(content):
            groups = match.groups()
            kind = groups[0] or "function"
            name = groups[1] or groups[2]
            if not name:
                continue
            line = content.count("\n", 0, match.start()) + 1
            result.symbols.append(ParsedSymbol(
                name=name,
                qualified_name=name,
                kind=kind.lower(),
                start_line=line,
                end_line=line,
                signature=name,
                docstring="",
                symbol_key=f"{path}::{name}:{line}",
                file_path=path,
            ))

        for match in IMPORT_FROM_RE.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            result.imports.append(ParsedImport(
                module=match.group(1),
                line_number=line,
                evidence=match.group(0).strip()[:200],
            ))

        for match in REQUIRE_RE.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            result.imports.append(ParsedImport(
                module=match.group(1),
                line_number=line,
                evidence=match.group(0)[:200],
            ))

        for match in ROUTE_RE.finditer(content):
            method = (match.group(1) or match.group(3) or "get").upper()
            route_path = match.group(4)
            line = content.count("\n", 0, match.start()) + 1
            result.routes.append(ParsedRoute(
                method=method,
                path=route_path,
                handler="route handler (text match)",
                line_number=line,
                framework="express",
            ))

        # Next.js page routes
        page_match = NEXT_PAGE_RE.search(path)
        if page_match:
            page_route = "/" + page_match.group(1).replace("[", ":").replace("]", "")
            result.routes.append(ParsedRoute(
                method="GET",
                path=page_route,
                handler=path,
                line_number=1,
                framework="nextjs",
            ))

        return result
