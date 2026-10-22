"""Require module and function docstrings, and no hash comments, in every Python file."""

import ast

import io

import tokenize

from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]

_PACKAGES = (_ROOT / "aeko", _ROOT / "tests")


def _python_files():
    """Return every Python source file under aeko/ and tests/."""

    files = []

    for package in _PACKAGES:
        files.extend(package.rglob("*.py"))

    return sorted(files)


def _relative(path: Path) -> str:
    """Return path relative to the repository root for assertion messages."""

    return path.relative_to(_ROOT).as_posix()


def test_python_modules_and_functions_are_documented_without_comments():
    """Fail when a module or function lacks a docstring, or a hash comment remains."""

    undocumented = []

    commented = []

    for path in _python_files():
        source = path.read_text(encoding="utf-8")

        tree = ast.parse(source, filename=str(path))

        label = _relative(path)

        if ast.get_docstring(tree) is None:
            undocumented.append(f"{label}: module")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is None:
                    undocumented.append(f"{label}: {node.name}")

        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                commented.append(f"{label}:{token.start[0]}")

    assert not undocumented, f"Missing docstrings: {undocumented}"

    assert not commented, f"Inline comments remain: {commented}"
