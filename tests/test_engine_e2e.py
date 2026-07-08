"""End-to-end pipeline tests on the bundled broken fixture repositories."""

import json
import shutil
from pathlib import Path

import pytest

from repomedic.engine import InvestigationEngine
from repomedic.events import EventBus, ListSink
from repomedic.execute.local_executor import LocalExecutor
from repomedic.investigate.heuristics import DeterministicInvestigator
from repomedic.models.investigation import InvestigationSession

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def snapshot(repo: Path) -> dict[str, bytes]:
    """Every file in the repo except session artifacts, byte-exact."""
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".repomedic" not in p.parts and "__pycache__" not in p.parts
    }


def run_engine(fixture: str, tmp_path: Path):
    repo = tmp_path / fixture
    shutil.copytree(FIXTURES / fixture, repo,
                    ignore=shutil.ignore_patterns(".repomedic", "__pycache__"))
    bus = EventBus("session-001")
    sink = ListSink()
    bus.subscribe(sink)
    session = InvestigationSession(session_id="session-001", repo_path=str(repo),
                                   executor="local")
    engine = InvestigationEngine(
        repo_path=repo, provider=DeterministicInvestigator(repo_root=repo),
        executor=LocalExecutor(), bus=bus, session=session,
    )
    before = snapshot(repo)
    result = engine.run()
    # Invariant: no source file of the target repository is ever modified.
    assert snapshot(repo) == before
    return result, sink, repo


@pytest.mark.e2e
def test_cache_bug_fixture_end_to_end(tmp_path):
    session, sink, repo = run_engine("cache-bug", tmp_path)

    assert session.state == "COMPLETE"
    assert session.root_cause is not None
    assert session.root_cause.file == "src/cache.py"
    assert session.root_cause.evidence_ids  # never a root cause without evidence
    top = session.hypothesis(session.root_cause.hypothesis_id)
    assert top.category == "shared_mutable_class_attr"
    assert top.confidence >= 0.8

    assert session.patch is not None
    assert "self._store = {}" in session.patch.diff
    assert session.validation.verdict == "accepted"
    assert session.validation.original_failures_passed
    assert session.validation.regression_passed
    assert session.validation.regression_total == 7

    stages = [e.stage for e in sink.events]
    for expected in ("INGEST", "GRAPH", "EXECUTE", "FAILURE", "INVESTIGATE",
                     "VERIFY", "ROOT_CAUSE", "PATCH", "VALIDATE", "REGRESSION",
                     "REPORT"):
        assert expected in stages

    # Reports exist and are substantive.
    md = Path(session.report_markdown_path).read_text(encoding="utf-8")
    assert "Root cause" in md and "src/cache.py" in md and "```diff" in md
    payload = json.loads(Path(session.report_json_path).read_text(encoding="utf-8"))
    assert payload["validation"]["verdict"] == "accepted"
    assert payload["timeline"]

    # The engine never mutates the target repository itself.
    assert "class MemoryCache" in (repo / "src" / "cache.py").read_text(encoding="utf-8")
    assert "_store = {}" in (repo / "src" / "cache.py").read_text(encoding="utf-8")


@pytest.mark.e2e
def test_schema_mismatch_fixture_end_to_end(tmp_path):
    session, sink, repo = run_engine("schema-mismatch", tmp_path)

    assert session.state == "COMPLETE"
    top = session.hypothesis(session.root_cause.hypothesis_id)
    assert top.category == "schema_key_mismatch"
    assert session.root_cause.file == "src/repository.py"
    assert top.experiment.verdict == "supports"

    assert '+        "username": name' in session.patch.diff
    assert session.validation.verdict == "accepted"
    assert session.validation.regression_total == 6

    # untouched source repo
    assert "user_name" in (repo / "src" / "repository.py").read_text(encoding="utf-8")


@pytest.mark.e2e
def test_green_repo_short_circuits(tmp_path):
    repo = tmp_path / "green"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("", encoding="utf-8")
    (repo / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")
    bus = EventBus("session-001")
    session = InvestigationSession(session_id="session-001", repo_path=str(repo),
                                   executor="local")
    engine = InvestigationEngine(
        repo_path=repo, provider=DeterministicInvestigator(repo_root=repo),
        executor=LocalExecutor(), bus=bus, session=session,
    )
    session = engine.run()
    assert session.state == "NO_FAILURE"
    assert session.root_cause is None
    assert session.report_markdown_path is not None


@pytest.mark.e2e
def test_unsupported_repo_fails_cleanly(tmp_path):
    repo = tmp_path / "mystery"
    repo.mkdir()
    (repo / "data.txt").write_text("not code", encoding="utf-8")
    bus = EventBus("session-001")
    session = InvestigationSession(session_id="session-001", repo_path=str(repo),
                                   executor="local")
    engine = InvestigationEngine(
        repo_path=repo, provider=DeterministicInvestigator(repo_root=repo),
        executor=LocalExecutor(), bus=bus, session=session,
    )
    session = engine.run()
    assert session.state == "FAILED"
    assert session.error == "unsupported repository"
