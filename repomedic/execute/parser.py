"""Parse pytest results into structured evidence.

Primary channel: JUnit XML written inside the workspace (reliable across
local and Docker execution). The failure `longrepr` text is additionally
parsed into stack frames. A stdout regex fallback covers runs where the XML
was never produced (e.g. collection crash).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from repomedic.execute.base import DEFAULT_TIMEOUT_S, JUNIT_FILENAME, Executor
from repomedic.models.execution import (
    CommandResult,
    ExecutionResult,
    Failure,
    Frame,
    TestCaseResult,
    TestOutcome,
)

# pytest longrepr frame: "src/api.py:12: in handler" or "src/api.py:12: KeyError"
_PYTEST_FRAME_RE = re.compile(
    r"^(?P<file>[^\s:][^:\n]*?\.py):(?P<line>\d+):"
    r"(?:\s+in\s+(?P<func>\S+)|\s+[A-Za-z_][\w.]*)?\s*$",
    re.M,
)
# CPython traceback frame: 'File "src/api.py", line 12, in handler'
_TB_FRAME_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)', re.M)
_EXC_RE = re.compile(r"^E?\s*(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Failed|Warning))(?::\s*(?P<msg>.*))?$", re.M)
_SUMMARY_RE = re.compile(
    r"=+ (?:(?P<failed>\d+) failed)?(?:, )?(?:(?P<passed>\d+) passed)?"
    r"(?:, )?(?:(?P<skipped>\d+) skipped)?(?:, )?(?:(?P<errors>\d+) errors?)?.* in "
)
_FAILED_LINE_RE = re.compile(r"^(?:FAILED|ERROR) (?P<id>\S+?)(?: - (?P<msg>.*))?$", re.M)


def _is_repo_path(file: str) -> bool:
    if "site-packages" in file or "importlib" in file:
        return False
    return not (file.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", file) or file.startswith("<"))


def parse_traceback(raw: str) -> list[Frame]:
    frames: list[Frame] = []
    lines = raw.splitlines()
    for match in _TB_FRAME_RE.finditer(raw):
        frames.append(Frame(
            file=match["file"].replace("\\", "/"),
            line=int(match["line"]),
            function=match["func"],
            in_repo=_is_repo_path(match["file"].replace("\\", "/")),
        ))
    if frames:
        return frames
    for match in _PYTEST_FRAME_RE.finditer(raw):
        file = match["file"].replace("\\", "/")
        lineno = int(match["line"])
        # Attach the first code line above the frame marker as context.
        code = ""
        for idx, text in enumerate(lines):
            if _PYTEST_FRAME_RE.match(text) and file in text.replace("\\", "/") and f":{lineno}:" in text.replace("\\", "/"):
                for back in range(idx - 1, -1, -1):
                    stripped = lines[back].strip()
                    if stripped and not stripped.startswith("E "):
                        code = stripped.lstrip("> ").strip()
                        break
                break
        frames.append(Frame(
            file=file, line=lineno, function=match["func"] or "",
            code=code, in_repo=_is_repo_path(file),
        ))
    return frames


def _first_line(text: str, limit: int = 300) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit]


def _exception_from_text(text: str) -> tuple[str, str]:
    last_type, last_msg = "", ""
    for match in _EXC_RE.finditer(text):
        last_type = match["type"].rpartition(".")[2]
        last_msg = (match["msg"] or "").strip()
    return last_type, last_msg


def parse_junit_xml(xml_text: str) -> tuple[list[TestCaseResult], list[Failure]]:
    tests: list[TestCaseResult] = []
    failures: list[Failure] = []
    root = ET.fromstring(xml_text)
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        file_attr = case.get("file", "")
        if file_attr:
            test_id = f"{file_attr.replace(chr(92), '/')}::{name}"
        else:
            test_id = f"{classname}::{name}" if classname else name
        duration = float(case.get("time", "0") or 0)

        node = None
        outcome = TestOutcome.PASSED
        for tag, out in (("failure", TestOutcome.FAILED), ("error", TestOutcome.ERROR),
                         ("skipped", TestOutcome.SKIPPED)):
            node = case.find(tag)
            if node is not None:
                outcome = out
                break

        message = node.get("message", "") if node is not None else ""
        tests.append(TestCaseResult(
            test_id=test_id, outcome=outcome, message=_first_line(message),
            duration_s=duration,
        ))
        if outcome in (TestOutcome.FAILED, TestOutcome.ERROR):
            raw = (node.text or "") if node is not None else ""
            exc_type, exc_msg = _exception_from_text(message + "\n" + raw)
            # Bare `assert` failures carry no exception name in the junit message.
            if not exc_type and message.lstrip().startswith("assert"):
                exc_type = "AssertionError"
            failures.append(Failure(
                test_id=test_id,
                exception_type=exc_type,
                message=_first_line(exc_msg or message),
                frames=parse_traceback(raw),
                raw=raw.strip(),
            ))
    return tests, failures


def parse_stdout_fallback(stdout: str) -> tuple[list[TestCaseResult], list[Failure]]:
    tests: list[TestCaseResult] = []
    failures: list[Failure] = []
    for match in _FAILED_LINE_RE.finditer(stdout):
        exc_type, exc_msg = _exception_from_text(match["msg"] or "")
        tests.append(TestCaseResult(
            test_id=match["id"], outcome=TestOutcome.FAILED, message=match["msg"] or "",
        ))
        failures.append(Failure(
            test_id=match["id"], exception_type=exc_type,
            message=exc_msg or (match["msg"] or ""), frames=parse_traceback(stdout),
            raw=stdout[-4000:],
        ))
    return tests, failures


def build_execution_result(command_result: CommandResult, junit_xml: str | None) -> ExecutionResult:
    if junit_xml:
        try:
            tests, failures = parse_junit_xml(junit_xml)
        except ET.ParseError:
            tests, failures = parse_stdout_fallback(command_result.stdout)
    else:
        tests, failures = parse_stdout_fallback(command_result.stdout)

    result = ExecutionResult(command_result=command_result, tests=tests, failures=failures)
    for test in tests:
        if test.outcome == TestOutcome.PASSED:
            result.passed += 1
        elif test.outcome == TestOutcome.FAILED:
            result.failed += 1
        elif test.outcome == TestOutcome.ERROR:
            result.errors += 1
        else:
            result.skipped += 1
    return result


def run_pytest(
    executor: Executor,
    workdir: Path,
    base_command: list[str] | None = None,
    extra_args: list[str] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> ExecutionResult:
    """Run pytest in the workspace and return structured results."""
    command = list(base_command or ["python", "-m", "pytest", "-q"])
    command += ["--junitxml", JUNIT_FILENAME, "-o", "junit_family=xunit2"]
    if extra_args:
        command += extra_args

    junit_path = Path(workdir) / JUNIT_FILENAME
    if junit_path.exists():
        junit_path.unlink()

    command_result = executor.run(Path(workdir), command, timeout_s=timeout_s)
    junit_xml = None
    if junit_path.exists():
        junit_xml = junit_path.read_text(encoding="utf-8", errors="replace")
        junit_path.unlink()
    return build_execution_result(command_result, junit_xml)
