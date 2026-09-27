"""Semantic chunking: produce meaningful chunks from analyzed repository files."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from backend.parsers.base import ParsedSymbol

LOGGER = logging.getLogger("onboard.chunker")

MAX_CHUNK_CHARS = 4_000
MIN_CHUNK_CHARS = 60


@dataclass
class Chunk:
    chunk_text: str
    chunk_type: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    symbol_name: str | None = None
    extra_meta: dict | None = None


def _language(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    return {
        "py": "python", "pyi": "python",
        "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
        "ts": "typescript", "tsx": "typescript",
        "vue": "vue", "svelte": "svelte",
        "java": "java", "kt": "kotlin", "go": "go", "rs": "rust",
        "rb": "ruby", "php": "php", "cs": "csharp", "c": "c", "cpp": "cpp", "h": "c",
        "sql": "sql", "md": "markdown", "mdx": "markdown", "rst": "rst",
        "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "xml": "xml",
        "html": "html", "css": "css", "scss": "scss",
        "sh": "shell", "bash": "shell",
        "tf": "terraform", "dockerfile": "dockerfile",
    }.get(suffix, suffix or "text")


def chunk_file(
    file_path: str,
    content: str,
    symbols: list[ParsedSymbol],
    kind: str = "source",
) -> list[Chunk]:
    """Produce semantic chunks for a single file."""
    chunks: list[Chunk] = []
    lang = _language(file_path)
    lines = content.splitlines()

    if not lines:
        return chunks

    if kind == "documentation":
        chunks.extend(_chunk_by_headings(file_path, content, lang))
        return chunks

    # For source files, chunk by symbol (function/class)
    if symbols:
        covered_ranges: list[tuple[int, int]] = []
        sorted_syms = sorted(symbols, key=lambda s: s.start_line)

        for sym in sorted_syms:
            # Only top-level symbols for chunking (skip nested methods individually,
            # they'll be included in class chunks)
            if "." in sym.qualified_name and sym.kind != "class":
                continue
            start = max(0, sym.start_line - 1)
            end = min(len(lines), sym.end_line)
            sym_lines = lines[start:end]
            sym_text = "\n".join(sym_lines)

            # Add header context
            header = f"# File: {file_path}\n# Symbol: {sym.qualified_name} ({sym.kind})\n# Lines: {sym.start_line}-{sym.end_line}\n\n"
            full_text = header + sym_text

            if len(full_text) < MIN_CHUNK_CHARS:
                continue

            # Split large symbols
            if len(full_text) > MAX_CHUNK_CHARS:
                for sub_chunk in _split_large(full_text, file_path, sym.start_line, lang, sym.qualified_name):
                    chunks.append(sub_chunk)
            else:
                chunks.append(Chunk(
                    chunk_text=full_text[:MAX_CHUNK_CHARS],
                    chunk_type=sym.kind,
                    file_path=file_path,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    language=lang,
                    symbol_name=sym.qualified_name,
                ))
            covered_ranges.append((sym.start_line, sym.end_line))

        # Chunk uncovered lines (top-of-file imports, module-level code)
        uncovered = _uncovered_lines(lines, covered_ranges)
        if uncovered and len("\n".join(uncovered)) >= MIN_CHUNK_CHARS:
            header = f"# File: {file_path}\n# Type: module-level\n\n"
            chunks.insert(0, Chunk(
                chunk_text=(header + "\n".join(uncovered))[:MAX_CHUNK_CHARS],
                chunk_type="module",
                file_path=file_path,
                start_line=1,
                end_line=len(lines),
                language=lang,
            ))
    else:
        # No symbols: chunk by fixed window
        chunks.extend(_sliding_window_chunks(file_path, lines, lang))

    return chunks


def _chunk_by_headings(file_path: str, content: str, lang: str) -> list[Chunk]:
    """Split markdown/docs by heading sections."""
    chunks: list[Chunk] = []
    lines = content.splitlines()
    current_heading = "Introduction"
    current_lines: list[str] = []
    current_start = 1

    for i, line in enumerate(lines, start=1):
        if line.startswith("#"):
            if current_lines and "\n".join(current_lines).strip():
                text = f"# File: {file_path}\n# Section: {current_heading}\n\n" + "\n".join(current_lines)
                if len(text) >= MIN_CHUNK_CHARS:
                    chunks.append(Chunk(
                        chunk_text=text[:MAX_CHUNK_CHARS],
                        chunk_type="documentation",
                        file_path=file_path,
                        start_line=current_start,
                        end_line=i - 1,
                        language=lang,
                        symbol_name=current_heading,
                    ))
            current_heading = line.lstrip("#").strip()
            current_lines = [line]
            current_start = i
        else:
            current_lines.append(line)

    # Flush last section
    if current_lines and "\n".join(current_lines).strip():
        text = f"# File: {file_path}\n# Section: {current_heading}\n\n" + "\n".join(current_lines)
        if len(text) >= MIN_CHUNK_CHARS:
            chunks.append(Chunk(
                chunk_text=text[:MAX_CHUNK_CHARS],
                chunk_type="documentation",
                file_path=file_path,
                start_line=current_start,
                end_line=len(lines),
                language=lang,
                symbol_name=current_heading,
            ))
    return chunks


def _uncovered_lines(lines: list[str], covered: list[tuple[int, int]]) -> list[str]:
    covered_set: set[int] = set()
    for start, end in covered:
        covered_set.update(range(start, end + 1))
    return [line for i, line in enumerate(lines, start=1) if i not in covered_set]


def _sliding_window_chunks(file_path: str, lines: list[str], lang: str, window: int = 50, overlap: int = 10) -> list[Chunk]:
    chunks: list[Chunk] = []
    step = max(1, window - overlap)
    for start in range(0, len(lines), step):
        end = min(len(lines), start + window)
        window_lines = lines[start:end]
        text = "\n".join(window_lines)
        if len(text) < MIN_CHUNK_CHARS:
            continue
        header = f"# File: {file_path}\n# Lines: {start + 1}-{end}\n\n"
        chunks.append(Chunk(
            chunk_text=(header + text)[:MAX_CHUNK_CHARS],
            chunk_type="text",
            file_path=file_path,
            start_line=start + 1,
            end_line=end,
            language=lang,
        ))
    return chunks


def _split_large(text: str, file_path: str, start_line: int, lang: str, symbol_name: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    lines = text.splitlines()
    for i in range(0, len(lines), 80):
        sub = "\n".join(lines[i: i + 80])
        if len(sub) < MIN_CHUNK_CHARS:
            continue
        chunks.append(Chunk(
            chunk_text=sub[:MAX_CHUNK_CHARS],
            chunk_type="symbol_part",
            file_path=file_path,
            start_line=start_line + i,
            end_line=start_line + i + 80,
            language=lang,
            symbol_name=symbol_name,
        ))
    return chunks
