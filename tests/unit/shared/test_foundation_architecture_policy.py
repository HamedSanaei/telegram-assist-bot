"""Enforce the provider-independent boundary of the T005 foundation."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath
from typing import cast

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_SHARED_ROOT = _SOURCE_ROOT / "telegram_assist_bot" / "shared"
_FOUNDATION_PATHS = (
    _SHARED_ROOT / "errors.py",
    _SHARED_ROOT / "observability" / "__init__.py",
    _SHARED_ROOT / "observability" / "context.py",
    _SHARED_ROOT / "observability" / "logging.py",
    _SHARED_ROOT / "observability" / "redaction.py",
    _SHARED_ROOT / "retry" / "__init__.py",
    _SHARED_ROOT / "retry" / "executor.py",
    _SHARED_ROOT / "retry" / "policy.py",
)
_FORBIDDEN_PROJECT_PREFIXES = (
    "telegram_assist_bot.application",
    "telegram_assist_bot.bootstrap",
    "telegram_assist_bot.domain",
    "telegram_assist_bot.infrastructure",
    "telegram_assist_bot.presentation",
    "telegram_assist_bot.workers",
)
_FORBIDDEN_SDK_PREFIXES = (
    "aiogram",
    "aiohttp",
    "anthropic",
    "apscheduler",
    "arq",
    "bson",
    "celery",
    "cohere",
    "dramatiq",
    "google.genai",
    "google.generativeai",
    "groq",
    "httpx",
    "mistralai",
    "motor",
    "openai",
    "pymongo",
    "pyrogram",
    "requests",
    "rq",
    "schedule",
    "telegram",
    "telethon",
    "urllib3",
)
_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module"})


def _module_name(path: Path) -> str:
    relative = path.relative_to(_SOURCE_ROOT).with_suffix("")
    parts = relative.parts[:-1] if path.name == "__init__.py" else relative.parts
    return ".".join(parts)


def _package_name(path: Path) -> str:
    module_name = _module_name(path)
    if path.name == "__init__.py":
        return module_name
    package_name, separator, _module_leaf = module_name.rpartition(".")
    assert separator
    return package_name


def _absolute_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        assert node.module is not None
        return node.module
    relative_name = "." * node.level + (node.module or "")
    return resolve_name(relative_name, _package_name(path))


def _matches_prefix(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(f"{prefix}.")


def _is_forbidden_import(module_name: str) -> bool:
    return any(
        _matches_prefix(module_name, prefix)
        for prefix in (*_FORBIDDEN_PROJECT_PREFIXES, *_FORBIDDEN_SDK_PREFIXES)
    )


def _dynamic_import_name(node: ast.Call) -> str | None:
    function = node.func
    if isinstance(function, ast.Name) and function.id in _DYNAMIC_IMPORT_CALLS:
        return function.id
    if isinstance(function, ast.Attribute) and function.attr == "import_module":
        return function.attr
    return None


def _architecture_violations(path: Path, tree: ast.Module) -> list[str]:
    relative_path = PurePosixPath(path.relative_to(_REPOSITORY_ROOT).as_posix())
    violations: list[str] = []
    for node in ast.walk(tree):
        line_number = cast("int", getattr(node, "lineno", 0))
        imported_modules: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            imported_modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            try:
                imported_modules = (_absolute_import_from(path, node),)
            except (ImportError, ValueError):
                violations.append(
                    f"{relative_path}:{line_number}: invalid relative import"
                )
                continue
        elif isinstance(node, ast.Call):
            dynamic_name = _dynamic_import_name(node)
            if dynamic_name is not None:
                violations.append(
                    f"{relative_path}:{line_number}: dynamic import via "
                    f"{dynamic_name} is forbidden"
                )

        violations.extend(
            f"{relative_path}:{line_number}: forbidden import {imported_module}"
            for imported_module in imported_modules
            if _is_forbidden_import(imported_module)
        )
    return violations


def test_foundation_modules_are_documented_and_provider_independent() -> None:
    """Keep foundation modules documented and free of outward dependencies."""
    missing = [path for path in _FOUNDATION_PATHS if not path.is_file()]
    assert not missing, "\n".join(str(path) for path in missing)

    violations: list[str] = []
    for path in _FOUNDATION_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if ast.get_docstring(tree, clean=False) is None:
            relative_path = path.relative_to(_REPOSITORY_ROOT).as_posix()
            violations.append(f"{relative_path}:1: public module has no docstring")
        violations.extend(_architecture_violations(path, tree))

    assert not violations, "\n".join(sorted(violations))
