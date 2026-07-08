"""Docker executor tests. The live test only runs with a reachable daemon."""

import shutil
from pathlib import Path

import pytest

from repomedic.execute.docker_executor import DockerExecutor, select_executor
from repomedic.execute.local_executor import LocalExecutor

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

docker_up = DockerExecutor().available()


def test_select_executor_local():
    assert isinstance(select_executor("local"), LocalExecutor)


def test_select_executor_docker_unavailable_raises(monkeypatch):
    monkeypatch.setattr(DockerExecutor, "available", lambda self: False)
    with pytest.raises(RuntimeError):
        select_executor("docker")
    assert isinstance(select_executor("auto"), LocalExecutor)


@pytest.mark.docker
@pytest.mark.skipif(not docker_up, reason="docker daemon not reachable")
def test_docker_executor_runs_pytest(tmp_path):
    from repomedic.execute.parser import run_pytest

    repo = tmp_path / "cache-bug"
    shutil.copytree(FIXTURES / "cache-bug", repo,
                    ignore=shutil.ignore_patterns(".repomedic", "__pycache__"))
    executor = DockerExecutor()
    executor.ensure_image()
    result = run_pytest(executor, repo, timeout_s=300)
    assert result.command_result.executor == "docker"
    assert result.failed == 2
    assert result.passed == 5
