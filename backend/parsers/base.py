"""Base parser interface and shared utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSymbol:
    name: str
    qualified_name: str
    kind: str  # function, class, method, interface, type, etc.
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    symbol_key: str = ""
    file_path: str = ""


@dataclass
class ParsedImport:
    module: str
    line_number: int
    evidence: str = ""


@dataclass
class ParsedRoute:
    method: str
    path: str
    handler: str
    line_number: int
    framework: str = ""


@dataclass
class ParseResult:
    symbols: list[ParsedSymbol] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    routes: list[ParsedRoute] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # {source_symbol, name, line_number}
    language: str = "unknown"


class BaseParser:
    language: str = "unknown"
    extensions: tuple[str, ...] = ()

    def parse(self, path: str, content: str) -> ParseResult:
        raise NotImplementedError
