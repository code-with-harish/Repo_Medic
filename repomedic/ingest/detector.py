"""Detect language, package manager, test framework and execution command."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Directories never worth scanning.
IGNORED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules",
    "__pycache__", ".repomedic", ".tox", ".mypy_cache", ".pytest_cache",
    "dist", "build", ".eggs",
}


@dataclass
class Detection:
    language: str
    package_manager: str
    test_framework: str
    test_command: list[str]


def iter_source_files(root: Path, suffix: str = ".py") -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob(f"*{suffix}")):
        if any(part in IGNORED_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return files


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect(root: Path) -> Detection:
    """Inspect marker files and layout to classify the repository."""
    root = Path(root)
    py_files = iter_source_files(root, ".py")
    has_package_json = (root / "package.json").exists()

    if not py_files and has_package_json:
        return Detection("javascript", "npm", "unknown", [])
    if not py_files:
        return Detection("unknown", "unknown", "unknown", [])

    package_manager = "pip"
    if (root / "poetry.lock").exists():
        package_manager = "poetry"
    elif (root / "uv.lock").exists():
        package_manager = "uv"
    elif (root / "Pipfile").exists():
        package_manager = "pipenv"
    elif (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        package_manager = "pip"

    test_framework = _detect_python_test_framework(root, py_files)
    if test_framework == "pytest":
        test_command = ["python", "-m", "pytest", "-q"]
    elif test_framework == "unittest":
        test_command = ["python", "-m", "unittest", "discover", "-v"]
    else:
        test_command = []

    return Detection("python", package_manager, test_framework, test_command)


def _detect_python_test_framework(root: Path, py_files: list[Path]) -> str:
    config_text = ""
    for marker in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        config_text += _read_text(root / marker)
    if "pytest" in config_text:
        return "pytest"

    test_files = [p for p in py_files if p.name.startswith("test_") or p.name.endswith("_test.py")]
    uses_unittest = False
    for path in test_files:
        text = _read_text(path)
        if "import pytest" in text or "from pytest" in text:
            return "pytest"
        if "import unittest" in text:
            uses_unittest = True
    if uses_unittest:
        return "unittest"
    # pytest runs plain assert-style test functions; default to it when tests exist.
    return "pytest" if test_files else "unknown"
