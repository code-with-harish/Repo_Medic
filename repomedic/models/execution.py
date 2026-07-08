"""Structured evidence captured from executing the repository's tests."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CommandResult(BaseModel):
    """Raw outcome of one command executed in an isolated environment."""

    command: list[str]
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0
    timed_out: bool = False
    executor: str = "local"  # "local" | "docker"
    cwd: str = ""


class Frame(BaseModel):
    """One stack-trace frame, normalized to repo-relative paths when possible."""

    file: str
    line: int
    function: str = ""
    code: str = ""
    in_repo: bool = False


class TestOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class TestCaseResult(BaseModel):
    """One test case parsed from the run."""

    test_id: str  # e.g. "tests/test_cache.py::test_isolation"
    outcome: TestOutcome
    message: str = ""
    duration_s: float = 0.0


class Failure(BaseModel):
    """A parsed failing test with its traceback evidence."""

    test_id: str
    exception_type: str = ""
    message: str = ""
    frames: list[Frame] = Field(default_factory=list)
    raw: str = ""

    def deepest_repo_frame(self) -> Frame | None:
        for frame in reversed(self.frames):
            if frame.in_repo:
                return frame
        return None


class ExecutionResult(BaseModel):
    """Full structured result of a test/reproduction run."""

    command_result: CommandResult
    tests: list[TestCaseResult] = Field(default_factory=list)
    failures: list[Failure] = Field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped

    @property
    def all_green(self) -> bool:
        return self.failed == 0 and self.errors == 0 and self.command_result.exit_code == 0
