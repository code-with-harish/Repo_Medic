"""Executor protocol and isolated workspace management.

RepoMedic never mutates or executes the user's repository in place. All
execution — test runs, experiments, patch validation — happens inside a
`Workspace`: a temporary copy of the repository handed to an `Executor`.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from repomedic.ingest.detector import IGNORED_DIRS
from repomedic.models.execution import CommandResult

JUNIT_FILENAME = ".repomedic-junit.xml"
DEFAULT_TIMEOUT_S = 300


@runtime_checkable
class Executor(Protocol):
    """Runs a command against a workspace directory and captures evidence."""

    name: str

    def available(self) -> bool: ...

    def run(
        self, workdir: Path, command: list[str], timeout_s: int = DEFAULT_TIMEOUT_S
    ) -> CommandResult: ...


class Workspace:
    """A disposable copy of a repository."""

    def __init__(self, source: Path, label: str = "ws") -> None:
        self.source = Path(source).resolve()
        self._tmp = tempfile.mkdtemp(prefix=f"repomedic-{label}-")
        self.path = Path(self._tmp) / self.source.name
        shutil.copytree(
            self.source,
            self.path,
            ignore=shutil.ignore_patterns(*IGNORED_DIRS, "*.pyc"),
        )

    def cleanup(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cleanup()
