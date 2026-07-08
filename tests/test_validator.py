from pathlib import Path

from repomedic.execute.local_executor import LocalExecutor
from repomedic.models.investigation import PatchProposal
from repomedic.patch.validator import validate_patch

PYTEST_CMD = ["python", "-m", "pytest", "-q"]


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    write(repo, "tests/test_calc.py", (
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(1, 1) == 2\n"
        "def test_add_zero():\n"
        "    assert add(0, 0) == 0\n"
    ))
    return repo


GOOD_DIFF = """\
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

BAD_DIFF = """\
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a * b
"""


def test_accepts_good_patch(tmp_path):
    repo = make_repo(tmp_path)
    patch = PatchProposal(diff=GOOD_DIFF, files=["calc.py"])
    result = validate_patch(repo, patch, ["tests/test_calc.py::test_add"],
                            PYTEST_CMD, LocalExecutor())
    assert result.verdict == "accepted"
    assert result.original_failures_passed
    assert result.regression_passed
    assert result.regression_total == 2
    # validation ran in a temp copy; original repo untouched
    assert "a - b" in (repo / "calc.py").read_text(encoding="utf-8")


def test_rejects_patch_that_does_not_fix(tmp_path):
    repo = make_repo(tmp_path)
    patch = PatchProposal(diff=BAD_DIFF, files=["calc.py"])
    result = validate_patch(repo, patch, ["tests/test_calc.py::test_add"],
                            PYTEST_CMD, LocalExecutor())
    assert result.verdict == "rejected_original"
    assert not result.original_failures_passed
    assert result.regression_run is None  # phase 2 never claimed to run


def test_rejects_patch_that_regresses(tmp_path):
    repo = make_repo(tmp_path)
    # Fixes test_add but breaks test_add_zero (0+0+1 != 0).
    diff = """\
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b if (a, b) != (0, 0) else 99
"""
    patch = PatchProposal(diff=diff, files=["calc.py"])
    result = validate_patch(repo, patch, ["tests/test_calc.py::test_add"],
                            PYTEST_CMD, LocalExecutor())
    assert result.verdict == "rejected_regression"
    assert result.original_failures_passed
    assert not result.regression_passed


def test_rejects_unappliable_patch(tmp_path):
    repo = make_repo(tmp_path)
    stale = GOOD_DIFF.replace("a - b", "totally stale context")
    result = validate_patch(repo, PatchProposal(diff=stale, files=["calc.py"]),
                            ["tests/test_calc.py::test_add"], PYTEST_CMD,
                            LocalExecutor())
    assert result.verdict == "rejected_original"
    assert result.original_run is None  # nothing was executed
