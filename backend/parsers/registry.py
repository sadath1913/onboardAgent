"""Parser registry — maps file extensions to parser instances."""

from __future__ import annotations

from pathlib import PurePosixPath

from backend.parsers.base import BaseParser, ParseResult
from backend.parsers.python_parser import PythonParser
from backend.parsers.js_parser import JSParser
from backend.parsers.generic_parser import GenericParser

_PYTHON = PythonParser()
_JS = JSParser()
_GENERIC = GenericParser()

_EXT_MAP: dict[str, BaseParser] = {}
for _parser in [_PYTHON, _JS]:
    for _ext in _parser.extensions:
        _EXT_MAP[_ext] = _parser


def get_parser(path: str) -> BaseParser:
    suffix = PurePosixPath(path).suffix.lower()
    return _EXT_MAP.get(suffix, _GENERIC)


def parse_file(path: str, content: str) -> ParseResult:
    return get_parser(path).parse(path, content)
