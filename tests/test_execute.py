from pathlib import Path

import pytest

from repomedic.execute.base import Workspace
from repomedic.execute.local_executor import LocalExecutor
from repomedic.execute.parser import parse_traceback, run_pytest


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def failing_repo(tmp_path):
    repo = tmp_path / "demo"
    write(repo, "conftest.py", "")
    write(repo, "src/__init__.py", "")
    write(repo, "src/api.py", (
        "def get_user():\n"
        "    return {'user_name': 'ada'}\n"
        "def handler():\n"
        "    return get_user()['username']\n"
    ))
    write(repo, "tests/test_api.py", (
        "from src.api import handler\n"
        "def test_handler():\n"
        "    assert handler() == 'ada'\n"
        "def test_passing():\n"
        "    assert 1 + 1 == 2\n"
    ))
    return repo


def test_workspace_is_a_copy(failing_repo):
    with Workspace(failing_repo) as ws:
        assert ws.path != failing_repo
        assert (ws.path / "src" / "api.py").exists()
        (ws.path / "src" / "api.py").write_text("changed", encoding="utf-8")
        assert "changed" not in (failing_repo / "src" / "api.py").read_text(encoding="utf-8")
    assert not ws.path.exists()


def test_local_executor_runs_and_captures(tmp_path):
    result = LocalExecutor().run(tmp_path, ["python", "-c", "print('hi'); import sys; sys.exit(3)"])
    assert result.exit_code == 3
    assert "hi" in result.stdout
    assert result.executor == "local"


def test_local_executor_timeout(tmp_path):
    result = LocalExecutor().run(
        tmp_path, ["python", "-c", "import time; time.sleep(30)"], timeout_s=2
    )
    assert result.timed_out
    assert result.exit_code == -1


def test_run_pytest_structured_results(failing_repo):
    with Workspace(failing_repo) as ws:
        result = run_pytest(LocalExecutor(), ws.path)
    assert result.failed == 1
    assert result.passed == 1
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.test_id.endswith("::test_handler")
    assert failure.exception_type == "KeyError"
    assert "username" in failure.message
    frame = failure.deepest_repo_frame()
    assert frame is not None
    assert frame.file == "src/api.py"
    assert frame.line == 4


def test_run_pytest_all_green(failing_repo):
    fixed = (
        "def get_user():\n"
        "    return {'username': 'ada'}\n"
        "def handler():\n"
        "    return get_user()['username']\n"
    )
    write(failing_repo, "src/api.py", fixed)
    with Workspace(failing_repo) as ws:
        result = run_pytest(LocalExecutor(), ws.path)
    assert result.all_green
    assert result.passed == 2


def test_parse_traceback_pytest_style():
    raw = (
        "tests/test_api.py:3: in test_handler\n"
        "    assert handler() == 'ada'\n"
        "src/api.py:4: in handler\n"
        "    return get_user()['username']\n"
        "E   KeyError: 'username'\n"
    )
    frames = parse_traceback(raw)
    assert [f.file for f in frames] == ["tests/test_api.py", "src/api.py"]
    assert frames[-1].line == 4
    assert all(f.in_repo for f in frames)


def test_parse_traceback_cpython_style():
    raw = (
        'Traceback (most recent call last):\n'
        '  File "src/app.py", line 10, in main\n'
        '    run()\n'
        '  File "C:\\Python311\\lib\\site-packages\\x.py", line 5, in run\n'
        '    boom()\n'
        'ValueError: boom\n'
    )
    frames = parse_traceback(raw)
    assert frames[0].in_repo
    assert not frames[1].in_repo
