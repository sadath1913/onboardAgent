"""Python AST-based parser."""

from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath

from backend.parsers.base import BaseParser, ParseResult, ParsedSymbol, ParsedImport, ParsedRoute

PY_ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "route"}


class PythonParser(BaseParser):
    language = "python"
    extensions = (".py", ".pyi")

    def parse(self, path: str, content: str) -> ParseResult:
        result = ParseResult(language="python")
        try:
            tree = ast.parse(content, filename=path)
        except (SyntaxError, ValueError):
            return result

        def _sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
            args = node.args
            positional = [*args.posonlyargs, *args.args]
            defaults_offset = len(positional) - len(args.defaults)
            parts: list[str] = []
            for i, arg in enumerate(positional):
                text = ast.unparse(arg)
                if i >= defaults_offset:
                    text += "=" + ast.unparse(args.defaults[i - defaults_offset])
                parts.append(text)
                if args.posonlyargs and i + 1 == len(args.posonlyargs):
                    parts.append("/")
            if args.vararg:
                parts.append("*" + ast.unparse(args.vararg))
            elif args.kwonlyargs:
                parts.append("*")
            for i, arg in enumerate(args.kwonlyargs):
                text = ast.unparse(arg)
                default = args.kw_defaults[i]
                if default is not None:
                    text += "=" + ast.unparse(default)
                parts.append(text)
            if args.kwarg:
                parts.append("**" + ast.unparse(args.kwarg))
            return f"{node.name}({', '.join(parts)})"

        def visit_scope(nodes: list, prefix: str = "") -> None:
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = node.name
                    qualified = f"{prefix}.{name}" if prefix else name
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    sig = _sig(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else name
                    sym = ParsedSymbol(
                        name=name,
                        qualified_name=qualified,
                        kind=kind,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        signature=sig,
                        docstring=ast.get_docstring(node) or "",
                        symbol_key=f"{path}::{qualified}:{node.lineno}",
                        file_path=path,
                    )
                    result.symbols.append(sym)
                    if isinstance(node, ast.ClassDef):
                        visit_scope(node.body, qualified)
                    else:
                        for child in ast.walk(node):
                            if isinstance(child, ast.Call):
                                called = ""
                                if isinstance(child.func, ast.Name):
                                    called = child.func.id
                                elif isinstance(child.func, ast.Attribute):
                                    called = child.func.attr
                                if called:
                                    result.calls.append({
                                        "source_symbol": f"{path}::{qualified}:{node.lineno}",
                                        "name": called,
                                        "line_number": child.lineno,
                                    })
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        result.imports.append(ParsedImport(
                            module=alias.name,
                            line_number=node.lineno,
                            evidence=f"import {alias.name}",
                        ))
                elif isinstance(node, ast.ImportFrom):
                    module = ("." * node.level) + (node.module or "")
                    result.imports.append(ParsedImport(
                        module=module,
                        line_number=node.lineno,
                        evidence=f"from {module} import ...",
                    ))

        visit_scope(tree.body)

        # Route extraction
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    target = decorator.func
                    method = target.attr.lower() if isinstance(target, ast.Attribute) else "route"
                    if method not in PY_ROUTE_METHODS:
                        continue
                    route_path = next(
                        (arg.value for arg in decorator.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str)),
                        None,
                    )
                    if route_path is None:
                        continue
                    methods = [method.upper()]
                    if method == "route":
                        methods = [
                            kw.value.elts[0].value.upper()
                            for kw in decorator.keywords
                            if kw.arg == "methods"
                            and isinstance(kw.value, (ast.List, ast.Tuple))
                            and kw.value.elts
                            and isinstance(kw.value.elts[0], ast.Constant)
                        ] or ["GET"]
                    framework = "flask"
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        framework = "fastapi" if "fast" in target.value.id.lower() else "flask"
                    for http_method in methods:
                        result.routes.append(ParsedRoute(
                            method=http_method,
                            path=route_path,
                            handler=node.name,
                            line_number=node.lineno,
                            framework=framework,
                        ))
        return result
