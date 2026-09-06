import ast
import io
import tokenize
from pathlib import Path


CONFIG_PACKAGE = Path(__file__).parents[1] / "aeko" / "config"


def _python_files():
    return sorted(CONFIG_PACKAGE.glob("*.py"))


def test_config_modules_and_public_functions_are_documented_without_comments():
    undocumented = []
    commented = []

    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if ast.get_docstring(tree) is None:
            undocumented.append(f"{path.name}: module")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                if ast.get_docstring(node) is None:
                    undocumented.append(f"{path.name}: {node.name}")

        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                commented.append(f"{path.name}:{token.start[0]}")

    assert not undocumented, f"Missing docstrings: {undocumented}"
    assert not commented, f"Inline comments remain: {commented}"
