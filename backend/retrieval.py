"""Hybrid RAG retrieval: vector similarity + keyword search + structural expansion."""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import text

from backend.config import settings
from backend.database import get_session
from backend.models import Chunk, Embedding, File, Symbol, Relationship

LOGGER = logging.getLogger("onboard.retrieval")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{1,}")
STOP_WORDS = {
    "where", "what", "which", "when", "does", "this", "that", "with", "from",
    "have", "into", "about", "file", "files", "please", "explain", "show",
    "how", "the", "and", "for", "are", "is", "was", "were", "can", "could",
    "would", "should", "it", "these", "those",
}


def _tokens(value: str) -> list[str]:
    result: list[str] = []
    for token in WORD_RE.findall(value):
        parts = [token.lower(), *[part.lower() for part in re.findall(r"[A-Za-z0-9]+", token)]]
        for part in parts:
            if len(part) > 1 and part not in STOP_WORDS and part not in result:
                result.append(part)
    return result[:12]


def _excerpt(content: str, terms: list[str], preferred_line: int | None = None, max_lines: int = 22) -> tuple[int, int, str]:
    lines = content.splitlines() or [""]
    if preferred_line is not None:
        center = max(0, min(len(lines) - 1, preferred_line - 1))
    else:
        ranked = []
        for idx, line in enumerate(lines):
            low = line.lower()
            score = sum(low.count(term) for term in terms)
            if score:
                ranked.append((score, idx))
        center = max(ranked)[1] if ranked else 0
    start = max(0, center - max_lines // 2)
    end = min(len(lines), start + max_lines)
    start = max(0, end - max_lines)
    excerpt_lines = []
    chars_used = 0
    for idx in range(start, end):
        line = lines[idx]
        if len(line) > 450:
            line = line[:450] + " … [line truncated]"
        rendered = f"{idx + 1}: {line}"
        if chars_used + len(rendered) > 3_000:
            excerpt_lines.append("[excerpt truncated to keep retrieval context bounded]")
            break
        excerpt_lines.append(rendered)
        chars_used += len(rendered) + 1
    return start + 1, end, "\n".join(excerpt_lines)


def vector_search(repository_id: str, query_embedding: list[float], top_k: int = 8) -> list[dict[str, Any]]:
    """Vector similarity search using pgvector cosine distance."""
    if not query_embedding:
        return []
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    sql = text("""
        SELECT
            c.id as chunk_id,
            c.file_path,
            c.chunk_text,
            c.chunk_type,
            c.start_line,
            c.end_line,
            c.language,
            c.symbol_name,
            1 - (e.embedding <=> :embedding::vector) as similarity
        FROM embeddings e
        JOIN chunks c ON c.id = e.chunk_id
        WHERE e.repository_id = :repo_id
        ORDER BY e.embedding <=> :embedding::vector
        LIMIT :top_k
    """)
    try:
        with get_session() as session:
            rows = session.execute(sql, {
                "embedding": embedding_str,
                "repo_id": repository_id,
                "top_k": top_k,
            }).fetchall()
        results = []
        for row in rows:
            results.append({
                "chunk_id": row.chunk_id,
                "path": row.file_path or "",
                "chunk_text": row.chunk_text,
                "chunk_type": row.chunk_type,
                "start_line": row.start_line,
                "end_line": row.end_line,
                "language": row.language,
                "symbol_name": row.symbol_name,
                "similarity": float(row.similarity) if row.similarity is not None else 0.0,
                "reason": "vector similarity",
            })
        return results
    except Exception as exc:
        LOGGER.warning("vector search failed: %s", exc)
        return []


def keyword_search(repository_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Keyword search over chunks using ILIKE."""
    terms = _tokens(query)
    if not terms:
        return []
    results = []
    with get_session() as session:
        for term in terms[:6]:
            rows = session.query(Chunk).filter(
                Chunk.repository_id == repository_id,
                Chunk.chunk_text.ilike(f"%{term}%"),
            ).limit(limit).all()
            for row in rows:
                results.append({
                    "chunk_id": row.id,
                    "path": row.file_path or "",
                    "chunk_text": row.chunk_text,
                    "chunk_type": row.chunk_type,
                    "start_line": row.start_line,
                    "end_line": row.end_line,
                    "language": row.language,
                    "symbol_name": row.symbol_name,
                    "similarity": 0.5,
                    "reason": "keyword match",
                })
    # Deduplicate
    seen: set[str] = set()
    unique = []
    for r in results:
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            unique.append(r)
    return unique[:limit]


def symbol_search(repository_id: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Search symbols by name and return their file context."""
    terms = _tokens(query)
    if not terms:
        return []
    results = []
    with get_session() as session:
        for term in terms[:4]:
            rows = session.query(Symbol, File).join(
                File, Symbol.file_id == File.id
            ).filter(
                Symbol.repository_id == repository_id,
                (Symbol.name.ilike(f"%{term}%")) | (Symbol.qualified_name.ilike(f"%{term}%")),
            ).limit(limit).all()
            for sym, file_row in rows:
                lines = file_row.content.splitlines() if file_row.content else []
                start = max(0, sym.start_line - 1)
                end = min(len(lines), sym.end_line)
                sym_text = "\n".join(lines[start:end])
                header = f"# Symbol: {sym.qualified_name} ({sym.kind})\n# File: {sym.file_path}:{sym.start_line}-{sym.end_line}\n\n"
                results.append({
                    "chunk_id": f"sym_{sym.id}",
                    "path": sym.file_path,
                    "chunk_text": (header + sym_text)[:3000],
                    "chunk_type": sym.kind,
                    "start_line": sym.start_line,
                    "end_line": sym.end_line,
                    "language": "unknown",
                    "symbol_name": sym.qualified_name,
                    "similarity": 0.7,
                    "reason": "symbol match",
                })
    seen: set[str] = set()
    unique = []
    for r in results:
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            unique.append(r)
    return unique


def hybrid_search(repository_id: str, query: str, top_k: int | None = None) -> list[dict[str, Any]]:
    """Hybrid retrieval: vector + keyword + symbol + relationship expansion."""
    k = top_k or settings.retrieval_top_k
    results: list[dict[str, Any]] = []

    # 1. Vector search (requires embeddings)
    try:
        from backend.embeddings import embed_query
        q_emb = embed_query(query)
        if q_emb:
            vector_results = vector_search(repository_id, q_emb, top_k=k * 2)
            results.extend(vector_results)
    except Exception as exc:
        LOGGER.warning("vector search unavailable: %s", exc)

    # 2. Keyword search over chunks
    kw_results = keyword_search(repository_id, query, limit=k * 2)
    seen_ids = {r["chunk_id"] for r in results}
    for r in kw_results:
        if r["chunk_id"] not in seen_ids:
            results.append(r)
            seen_ids.add(r["chunk_id"])

    # 3. Symbol name search
    sym_results = symbol_search(repository_id, query, limit=k)
    for r in sym_results:
        if r["chunk_id"] not in seen_ids:
            results.append(r)
            seen_ids.add(r["chunk_id"])

    # 4. Sort by similarity score descending
    results.sort(key=lambda x: -x.get("similarity", 0))

    # 5. Relationship expansion — add related file chunks for top results
    top_paths = list(dict.fromkeys(r["path"] for r in results[:5] if r["path"]))
    if top_paths and len(results) < k * 3:
        expanded = _expand_by_relationships(repository_id, top_paths, seen_ids, limit=3)
        results.extend(expanded)

    # Format as evidence items (compatible with existing service code)
    evidence = []
    for r in results[:k]:
        lines = r["chunk_text"].splitlines() or [""]
        excerpt = "\n".join(f"{r['start_line'] + i}: {line}" for i, line in enumerate(lines[:22]))
        evidence.append({
            "path": r["path"],
            "kind": r.get("chunk_type", "source"),
            "score": r.get("similarity", 0),
            "start_line": r["start_line"],
            "end_line": r["end_line"],
            "excerpt": excerpt[:3000],
            "symbol": {"name": r["symbol_name"]} if r.get("symbol_name") else None,
            "reason": r.get("reason", "retrieval"),
        })
    return evidence


def _expand_by_relationships(repository_id: str, paths: list[str], seen_ids: set[str], limit: int = 3) -> list[dict[str, Any]]:
    """Find chunks from files related to the top results via import/call relationships."""
    extra: list[dict[str, Any]] = []
    try:
        with get_session() as session:
            for path in paths:
                rels = session.query(Relationship).filter(
                    Relationship.repository_id == repository_id,
                    Relationship.source_type == "file",
                    Relationship.source_key == path,
                    Relationship.target_type == "file",
                ).limit(5).all()
                for rel in rels:
                    related_chunks = session.query(Chunk).filter(
                        Chunk.repository_id == repository_id,
                        Chunk.file_path == rel.target_key,
                    ).limit(2).all()
                    for chunk in related_chunks:
                        if chunk.id not in seen_ids:
                            seen_ids.add(chunk.id)
                            lines = chunk.chunk_text.splitlines() or [""]
                            excerpt = "\n".join(f"{chunk.start_line + i}: {line}" for i, line in enumerate(lines[:22]))
                            extra.append({
                                "path": chunk.file_path or "",
                                "kind": chunk.chunk_type,
                                "score": 0.0,
                                "start_line": chunk.start_line,
                                "end_line": chunk.end_line,
                                "excerpt": excerpt[:3000],
                                "symbol": {"name": chunk.symbol_name} if chunk.symbol_name else None,
                                "reason": "direct repository relationship",
                                "chunk_id": chunk.id,
                            })
                            if len(extra) >= limit:
                                return extra
    except Exception as exc:
        LOGGER.warning("relationship expansion failed: %s", exc)
    return extra


# Legacy search_repository kept for backward compatibility
def search_repository(repository_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    return hybrid_search(repository_id, query, top_k=limit)
