"""Enforce the standard-library-only boundary of the post domain package."""

from __future__ import annotations

import ast
import inspect
import sys
from dataclasses import is_dataclass
from enum import Enum
from importlib import import_module
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath
from typing import cast

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_DOMAIN_ROOT = _SOURCE_ROOT / "telegram_assist_bot" / "domain"
_DOMAIN_PACKAGE = "telegram_assist_bot.domain"
_POSTS_PACKAGE = f"{_DOMAIN_PACKAGE}.posts"
_REQUIRED_PUBLIC_EXPORTS = frozenset(
    {
        "OriginalPostContent",
        "Post",
        "PostId",
        "PostStatus",
        "SourceMessageIdentity",
        "StatusTransition",
        "TelegramEntity",
        "TransitionActorCategory",
    }
)
_REQUIRED_FROZEN_MODELS = frozenset(
    {
        "OriginalPostContent",
        "Post",
        "PostId",
        "SourceMessageIdentity",
        "StatusTransition",
        "TelegramEntity",
    }
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


def _is_allowed_import(module_name: str) -> bool:
    root_name = module_name.partition(".")[0]
    is_domain_import = module_name == _DOMAIN_PACKAGE or module_name.startswith(
        f"{_DOMAIN_PACKAGE}."
    )
    return (
        is_domain_import
        or root_name in sys.stdlib_module_names
        or root_name == "__future__"
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
            if not _is_allowed_import(imported_module)
        )
    return violations


def test_domain_sources_only_import_stdlib_or_domain_modules() -> None:
    """Every Domain source stays independent of config, adapters, and SDKs."""
    source_paths = tuple(sorted(_DOMAIN_ROOT.rglob("*.py")))
    assert source_paths
    violations: list[str] = []

    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if ast.get_docstring(tree, clean=False) is None:
            relative_path = path.relative_to(_REPOSITORY_ROOT).as_posix()
            violations.append(f"{relative_path}:1: public module has no docstring")
        violations.extend(_architecture_violations(path, tree))

    assert not violations, "\n".join(sorted(violations))


def test_posts_package_has_documented_complete_public_exports() -> None:
    """The package exposes its stable Domain API without leaking private names."""
    posts = import_module(_POSTS_PACKAGE)
    assert inspect.getdoc(posts)
    raw_exports: object = getattr(posts, "__all__", None)
    assert isinstance(raw_exports, tuple)
    assert all(isinstance(name, str) for name in raw_exports)
    exports = cast("tuple[str, ...]", raw_exports)

    assert len(exports) == len(set(exports))
    assert all(name and not name.startswith("_") for name in exports)
    assert set(exports) >= _REQUIRED_PUBLIC_EXPORTS
    for name in exports:
        exported = getattr(posts, name)
        defining_module = getattr(exported, "__module__", "")
        if isinstance(exported, type) and defining_module.startswith(_POSTS_PACKAGE):
            assert inspect.getdoc(exported), f"public export {name} has no docstring"

    owned_public_names = {
        name
        for name, value in vars(posts).items()
        if not name.startswith("_")
        and str(getattr(value, "__module__", "")).startswith(_POSTS_PACKAGE)
    }
    assert owned_public_names <= set(exports)


def test_public_value_objects_are_frozen_and_status_is_an_enum() -> None:
    """Core immutable exports cannot be mutated through their public API."""
    posts = import_module(_POSTS_PACKAGE)
    for name in sorted(_REQUIRED_FROZEN_MODELS):
        model = getattr(posts, name)
        assert isinstance(model, type)
        assert is_dataclass(model), f"public model {name} must be a dataclass"
        parameters = getattr(model, "__dataclass_params__", None)
        assert parameters is not None
        assert parameters.frozen

    post_status = posts.PostStatus
    actor_category = posts.TransitionActorCategory
    assert isinstance(post_status, type)
    assert issubclass(post_status, Enum)
    assert isinstance(actor_category, type)
    assert issubclass(actor_category, Enum)
