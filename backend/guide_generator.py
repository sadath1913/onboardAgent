"""LLM-based developer onboarding guide generation."""

from __future__ import annotations

import logging
from typing import Any

from backend.llm import FallbackLLM

LOGGER = logging.getLogger("onboard.guide")

GUIDE_SYSTEM_PROMPT = """You are an expert developer onboarding assistant. Your job is to generate
a comprehensive, detailed developer onboarding guide for a new developer joining a project.

Write in clear, simple English that a new developer can easily understand.
Use Markdown formatting throughout.
Base your guide ONLY on the repository evidence provided.
Clearly distinguish observed facts from reasonable inferences.
Never fabricate information not supported by the evidence.
If you cannot determine something from the evidence, say so explicitly.
"""


def generate_llm_guide(
    analysis: dict[str, Any],
    repository: dict[str, Any],
    llm: FallbackLLM | None = None,
) -> dict[str, Any]:
    """Generate developer onboarding guide using LLM or fall back to static generation."""
    from backend.analyzer import generate_guide as static_guide
    static = static_guide(analysis, repository)

    if llm is None or not llm.enabled:
        LOGGER.info("LLM not available, using static guide generation")
        static["content_md"] = _static_to_markdown(static, repository)
        return static

    context = _build_llm_context(analysis, static, repository)
    messages = [
        {"role": "system", "content": GUIDE_SYSTEM_PROMPT},
        {"role": "user", "content": context},
    ]

    LOGGER.info("generating LLM onboarding guide repository=%s/%s", repository.get("owner"), repository.get("name"))
    guide_md = llm.complete(messages, temperature=0.2, max_tokens=8000)

    if not guide_md:
        LOGGER.warning("LLM guide generation failed, falling back to static guide")
        static["content_md"] = _static_to_markdown(static, repository)
        return static

    static["content_md"] = guide_md
    LOGGER.info("LLM guide generated length=%d chars", len(guide_md))
    return static


def _build_llm_context(analysis: dict[str, Any], static: dict[str, Any], repository: dict[str, Any]) -> str:
    """Build the LLM prompt context from analysis data."""
    repo_name = f"{repository.get('owner', 'unknown')}/{repository.get('name', 'unknown')}"
    parts = [f"Generate a comprehensive developer onboarding guide for the repository: **{repo_name}**\n"]

    # README
    files = analysis.get("files", [])
    readme = next((f for f in files if f["path"].lower().startswith("readme")), None)
    if readme:
        parts.append(f"## README\n{readme['content'][:3000]}\n")

    # Project structure
    structure = static.get("structure", [])
    if structure:
        parts.append("## Top-Level Directory Structure")
        for d in structure[:15]:
            parts.append(f"- `{d['path']}/` — {d['file_count']} files (examples: {', '.join(d['examples'][:3])})")

    # Tech stack and dependencies
    stack = static.get("overview", {}).get("technology_stack", [])
    deps = static.get("dependencies", [])
    if stack or deps:
        parts.append(f"\n## Technology Stack\nDetected: {', '.join(stack)}")
        if deps:
            parts.append(f"Key dependencies: {', '.join(deps[:30])}")

    # Entry points
    entry_points = static.get("entry_points", [])
    if entry_points:
        parts.append(f"\n## Entry Points\n{chr(10).join('- ' + ep for ep in entry_points[:10])}")

    # API routes
    routes = static.get("api_routes", [])
    if routes:
        parts.append(f"\n## API Routes ({len(routes)} total, showing first 30)")
        for route in routes[:30]:
            parts.append(f"- {route['method']} {route['path']} → {route['handler']} ({route['file_path']}:{route['line_number']})")

    # Key symbols (top 40)
    symbols = analysis.get("symbols", [])
    if symbols:
        parts.append(f"\n## Key Symbols ({len(symbols)} total, showing top 40)")
        classes = [s for s in symbols if s["kind"] == "class"][:15]
        functions = [s for s in symbols if s["kind"] == "function"][:25]
        for s in classes + functions:
            doc = s.get("docstring", "")[:100]
            parts.append(f"- **{s['qualified_name']}** ({s['kind']}) in `{s['file_path']}`:{s['start_line']}" + (f" — {doc}" if doc else ""))

    # Environment variables
    env_vars = static.get("environment_variables", [])
    if env_vars:
        parts.append(f"\n## Environment Variables\n{', '.join(env_vars[:30])}")

    # File kinds
    counts = static.get("overview", {}).get("counts", {})
    parts.append(f"\n## Repository Statistics\nFiles: {counts.get('files', 0)}, Symbols: {counts.get('symbols', 0)}, API Routes: {counts.get('api_routes', 0)}")

    # Key source files (first 20)
    source_files = [f for f in files if f["kind"] == "source"][:20]
    if source_files:
        parts.append("\n## Important Source Files")
        for f in source_files:
            parts.append(f"- `{f['path']}` ({f['line_count']} lines)")

    # Deployment files
    deploy = static.get("deployment_files", [])
    if deploy:
        parts.append(f"\n## Deployment / DevOps Files\n{chr(10).join('- ' + d for d in deploy[:10])}")

    parts.append("""
## Required Guide Sections

Please generate a complete onboarding guide with these sections:

1. Project Overview (what it does, who uses it, main purpose)
2. How the Application Works (high-level flow with Mermaid diagram if applicable)
3. Project Structure (important directories and their responsibilities)
4. Application Entry Points (where to start reading)
5. Features (feature-by-feature explanation with files, APIs, flow)
6. Important Functions and Classes (with purpose, inputs, outputs)
7. API Documentation (if API routes exist)
8. Database Architecture (if detected)
9. Architecture Overview
10. Data Flow (end-to-end flows)
11. Dependencies and Technologies
12. Configuration and Environment Variables
13. Testing (framework and structure)
14. Deployment / DevOps (if found)
15. How to Start Developing (practical steps)
16. Feature → File Map
17. Important Things New Developers Should Know
18. Limitations / Unknown Areas

Be detailed but clear. Write for a developer who has never seen this codebase before.
""")

    return "\n".join(parts)


def _static_to_markdown(static: dict[str, Any], repository: dict[str, Any]) -> str:
    """Convert static guide dict to markdown string."""
    repo_name = f"{repository.get('owner', 'unknown')}/{repository.get('name', 'unknown')}"
    lines = [f"# Developer Onboarding Guide: {repo_name}\n"]

    overview = static.get("overview", {})
    if overview.get("description"):
        lines.append("## Project Overview\n")
        lines.append(overview["description"][:2000])
        lines.append("")

    stack = overview.get("technology_stack", [])
    if stack:
        lines.append(f"**Technology Stack:** {', '.join(stack)}\n")

    structure = static.get("structure", [])
    if structure:
        lines.append("## Project Structure\n")
        for d in structure[:10]:
            lines.append(f"- `{d['path']}/` — {d['file_count']} files")
        lines.append("")

    entry_points = static.get("entry_points", [])
    if entry_points:
        lines.append("## Entry Points\n")
        for ep in entry_points:
            lines.append(f"- `{ep}`")
        lines.append("")

    routes = static.get("api_routes", [])
    if routes:
        lines.append("## API Routes\n")
        for r in routes[:30]:
            lines.append(f"- `{r['method']} {r['path']}` → `{r['handler']}` in `{r['file_path']}`")
        lines.append("")

    deps = static.get("dependencies", [])
    if deps:
        lines.append(f"## Dependencies\n{', '.join(deps[:30])}\n")

    env_vars = static.get("environment_variables", [])
    if env_vars:
        lines.append(f"## Environment Variables\n{', '.join(env_vars)}\n")

    limitations = static.get("limitations", [])
    if limitations:
        lines.append("## Limitations\n")
        for lim in limitations:
            lines.append(f"- {lim}")

    return "\n".join(lines)
