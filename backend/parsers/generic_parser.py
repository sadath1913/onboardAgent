"""Generic parser for config, data, and documentation files."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from backend.parsers.base import BaseParser, ParseResult, ParsedSymbol

SQL_OBJECT_RE = re.compile(
    r"^\s*CREATE\s+(?:TABLE|VIEW|FUNCTION|PROCEDURE|INDEX|SEQUENCE|TYPE)\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\"\[\w\.]+)",
    re.I | re.M,
)
YAML_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*$", re.M)


class GenericParser(BaseParser):
    language = "generic"
    extensions = ()

    def parse(self, path: str, content: str) -> ParseResult:
        suffix = PurePosixPath(path).suffix.lower()
        name = PurePosixPath(path).name.lower()

        if suffix == ".sql" or name.endswith(".sql"):
            return self._parse_sql(path, content)
        if suffix in (".md", ".mdx", ".rst"):
            return self._parse_markdown(path, content)
        return ParseResult(language="config")

    def _parse_sql(self, path: str, content: str) -> ParseResult:
        result = ParseResult(language="sql")
        for match in SQL_OBJECT_RE.finditer(content):
            obj_name = match.group(1).strip("`\"[]")
            line = content.count("\n", 0, match.start()) + 1
            kind_match = re.search(r"CREATE\s+(\w+)", match.group(0), re.I)
            kind = kind_match.group(1).lower() if kind_match else "object"
            result.symbols.append(ParsedSymbol(
                name=obj_name,
                qualified_name=obj_name,
                kind=kind,
                start_line=line,
                end_line=line,
                signature=f"CREATE {kind.upper()} {obj_name}",
                file_path=path,
                symbol_key=f"{path}::{obj_name}:{line}",
            ))
        return result

    def _parse_markdown(self, path: str, content: str) -> ParseResult:
        result = ParseResult(language="markdown")
        for match in re.finditer(r"^#{1,3}\s+(.+)$", content, re.M):
            heading = match.group(1).strip()
            line = content.count("\n", 0, match.start()) + 1
            result.symbols.append(ParsedSymbol(
                name=heading,
                qualified_name=heading,
                kind="section",
                start_line=line,
                end_line=line,
                signature=f"## {heading}",
                file_path=path,
                symbol_key=f"{path}::heading:{line}",
            ))
        return result
