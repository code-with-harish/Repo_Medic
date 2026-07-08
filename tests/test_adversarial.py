"""Adversarial audit tests: RepoMedic must fail honestly.

Covered here:
- syntax error preventing collection
- innocent mutable default (not responsible for the failure)
- wrong schema-rename guess / patch that regresses unrelated tests
- no confident root cause (fallback-only hypotheses)
- inconclusive verification experiments
- root-cause-requires-evidence invariant (hostile provider)
- confidence bounds under repeated updates
- patch applier: multi-hunk, multi-file atomicity, repeated lines,
  missing trailing newline, CRLF input
"""

from pathlib import Path

import pytest

from repomedic.engine import InvestigationEngine
from repomedic.events import EventBus, ListSink
from repomedic.execute.local_executor import LocalExecutor
from repomedic.investigate.experiments import update_confidence
from repomedic.investigate.heuristics import DeterministicInvestigator
from repomedic.models.investigation import (
    Experiment,
    Hypothesis,
    HypothesisStatus,
    InvestigationSession,
    SuspectLocation,
)
from repomedic.patch.applier import PatchError, apply_unified_diff


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(repo: Path, provider=None):
    bus = EventBus("session-001")
    sink = ListSink()
    bus.subscribe(sink)
    session = InvestigationSession(session_id="session-001", repo_path=str(repo),
                                   executor="local")
    engine = InvestigationEngine(
        repo_path=repo,
        provider=provider or DeterministicInvestigator(repo_root=repo),
        executor=LocalExecutor(), bus=bus, session=session,
    )
    return engine.run(), sink


def stages(sink: ListSink) -> list[str]:
    return [e.stage for e in sink.events]


def messages(sink: ListSink, stage: str) -> list[str]:
    return [e.message for e in sink.events if e.stage == stage]


# --------------------------------------------------------------------- #
# honest failure paths
# --------------------------------------------------------------------- #

@pytest.mark.e2e
def test_syntax_error_fails_honestly(tmp_path):
    """A source syntax error breaks collection; no root cause is invented."""
    repo = tmp_path / "broken-syntax"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b)\n    return a + b\n")  # missing colon
    write(repo, "tests/test_calc.py",
          "from calc import add\ndef test_add():\n    assert add(1, 1) == 2\n")
    session, sink = run(repo)
    assert session.state == "FAILED"
    assert session.root_cause is None
    assert session.patch is None
    assert "ROOT_CAUSE" not in stages(sink)
    # the report still exists and is truthful
    md = Path(session.report_markdown_path).read_text(encoding="utf-8")
    assert "No root cause met the evidence-backed confidence threshold" in md
    assert "```diff" not in md


@pytest.mark.e2e
def test_innocent_mutable_default_is_rejected(tmp_path):
    """A mutable default exists but is NOT the cause: the failing test also
    fails in isolation, so the experiment contradicts and the hypothesis is
    rejected instead of becoming a false-positive root cause."""
    repo = tmp_path / "innocent-default"
    write(repo, "conftest.py", "")
    write(repo, "app.py", (
        "def tag(item, labels=[]):\n"          # smelly but never mutated
        "    return list(labels) + [item]\n"
        "def price(x):\n"
        "    return x * 3\n"                    # actual bug: should be * 2
    ))
    write(repo, "tests/test_app.py", (
        "from app import price\n"
        "def test_price():\n"
        "    assert price(2) == 4\n"
    ))
    session, sink = run(repo)
    assert session.state == "FAILED"
    assert session.root_cause is None
    assert "ROOT_CAUSE" not in stages(sink)
    mutable = [h for h in session.hypotheses
               if h.category == "mutable_default_argument"]
    assert mutable and all(h.status == HypothesisStatus.REJECTED for h in mutable)
    assert all(h.contradicting_evidence for h in mutable)


@pytest.mark.e2e
def test_patch_that_regresses_is_rejected(tmp_path):
    """Multiple unrelated failures: the schema rename fixes the original
    failing test but breaks a consumer of the old key. REGRESSION must FAIL
    and the patch must be rejected, never reported as success."""
    repo = tmp_path / "regressing-rename"
    write(repo, "conftest.py", "")
    write(repo, "svc.py", (
        "def record():\n"
        "    return {'user_name': 'ada'}\n"
    ))
    write(repo, "tests/test_new.py", (
        "from svc import record\n"
        "def test_new_contract():\n"
        "    assert record()['username'] == 'ada'\n"   # fails: KeyError
    ))
    write(repo, "tests/test_old.py", (
        "from svc import record\n"
        "def test_old_contract():\n"
        "    assert record()['user_name'] == 'ada'\n"  # passes; breaks after rename
    ))
    session, sink = run(repo)
    assert session.root_cause is not None            # investigation did localize
    assert session.patch is not None                 # and proposed a patch
    assert session.validation.verdict == "rejected_regression"
    assert session.validation.original_failures_passed
    assert not session.validation.regression_passed
    # events must not claim regression success
    assert any("FAIL" in m for m in messages(sink, "REGRESSION"))
    assert not any(": PASS" in m for m in messages(sink, "REGRESSION"))
    md = Path(session.report_markdown_path).read_text(encoding="utf-8")
    assert "rejected_regression" in md


@pytest.mark.e2e
def test_no_confident_root_cause(tmp_path):
    """Plain wrong logic matches no defect pattern: fallback hypothesis stays
    below threshold and the investigation ends FAILED."""
    repo = tmp_path / "no-pattern"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    write(repo, "tests/test_calc.py",
          "from calc import add\ndef test_add():\n    assert add(1, 1) == 2\n")
    session, sink = run(repo)
    assert session.state == "FAILED"
    assert session.root_cause is None
    assert session.hypotheses  # it did hypothesize...
    assert all(h.confidence < 0.6 for h in session.hypotheses)  # ...honestly


class OverconfidentProvider:
    """Hostile provider: high confidence, zero evidence, no experiment."""

    name = "overconfident"

    def generate_hypotheses(self, repo, execution):
        return [Hypothesis(
            hypothesis_id="H1", description="trust me", category="vibes",
            prior=0.99, confidence=0.99,
            suspect=SuspectLocation(file="calc.py", line=1),
        )]

    def propose_patch(self, repo, hypothesis):
        return None


@pytest.mark.e2e
def test_root_cause_requires_recorded_evidence(tmp_path):
    """Even a 0.99-confidence hypothesis is not selected without evidence."""
    repo = tmp_path / "no-evidence"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    write(repo, "tests/test_calc.py",
          "from calc import add\ndef test_add():\n    assert add(1, 1) == 2\n")
    session, sink = run(repo, provider=OverconfidentProvider())
    assert session.state == "FAILED"
    assert session.root_cause is None
    assert "ROOT_CAUSE" not in stages(sink)


class InconclusiveProvider(OverconfidentProvider):
    """Mid-confidence hypothesis whose experiment yields no verdict."""

    name = "inconclusive"

    def generate_hypotheses(self, repo, execution):
        hyp = super().generate_hypotheses(repo, execution)[0]
        hyp.prior = hyp.confidence = 0.5
        hyp.patch_context["static_findings"] = ["static: suspicious line"]
        hyp.experiment = Experiment(
            experiment_id="X1", description="says nothing",
            script="print('no verdict here')\n",
        )
        return [hyp]


@pytest.mark.e2e
def test_inconclusive_experiment_does_not_move_confidence(tmp_path):
    repo = tmp_path / "inconclusive"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b):\n    return a - b\n")
    write(repo, "tests/test_calc.py",
          "from calc import add\ndef test_add():\n    assert add(1, 1) == 2\n")
    session, sink = run(repo, provider=InconclusiveProvider())
    hyp = session.hypotheses[0]
    assert hyp.experiment.verdict == "inconclusive"
    assert hyp.confidence == 0.5                      # unchanged
    assert hyp.status == HypothesisStatus.OPEN
    assert session.state == "FAILED"                  # 0.5 < 0.6: no root cause
    assert session.root_cause is None


class CrashingExecutor:
    """Simulates a test command dying before any results are produced."""

    name = "local"

    def available(self):
        return True

    def run(self, workdir, command, timeout_s=300):
        from repomedic.models.execution import CommandResult
        return CommandResult(command=list(command), exit_code=2,
                             stdout="internal error: boom", executor=self.name)


@pytest.mark.e2e
def test_unparseable_run_reports_honestly(tmp_path):
    """Exit != 0 with no parseable results must not claim 'N failing tests'."""
    repo = tmp_path / "crash"
    write(repo, "conftest.py", "")
    write(repo, "calc.py", "def add(a, b):\n    return a + b\n")
    write(repo, "tests/test_calc.py",
          "from calc import add\ndef test_add():\n    assert add(1, 1) == 2\n")
    bus = EventBus("session-001")
    sink = ListSink()
    bus.subscribe(sink)
    session = InvestigationSession(session_id="session-001", repo_path=str(repo),
                                   executor="local")
    engine = InvestigationEngine(
        repo_path=repo, provider=DeterministicInvestigator(repo_root=repo),
        executor=CrashingExecutor(), bus=bus, session=session,
    )
    session = engine.run()
    assert session.state == "FAILED"
    failure_msgs = messages(sink, "FAILURE")
    assert failure_msgs == ["test command exited 2 with no parseable test results"]
    assert any("No hypotheses generated" in m for m in messages(sink, "INVESTIGATE"))


def test_confidence_stays_in_unit_interval():
    hyp = Hypothesis(hypothesis_id="H1", description="d", category="c",
                     prior=0.5, confidence=0.5)
    for _ in range(20):
        update_confidence(hyp, "supports")
        assert 0.0 <= hyp.confidence <= 1.0
    for _ in range(20):
        update_confidence(hyp, "contradicts")
        assert 0.0 <= hyp.confidence <= 1.0


# --------------------------------------------------------------------- #
# patch applier abuse
# --------------------------------------------------------------------- #

def test_multi_hunk_diff_applies_completely(tmp_path):
    original = "".join(f"line {i}\n" for i in range(1, 21))
    write(tmp_path, "big.py", original)
    diff = (
        "--- a/big.py\n"
        "+++ b/big.py\n"
        "@@ -1,3 +1,3 @@\n"
        "-line 1\n"
        "+LINE 1\n"
        " line 2\n"
        " line 3\n"
        "@@ -18,3 +18,3 @@\n"
        " line 18\n"
        "-line 19\n"
        "+LINE 19\n"
        " line 20\n"
    )
    apply_unified_diff(tmp_path, diff)
    text = (tmp_path / "big.py").read_text(encoding="utf-8")
    assert "LINE 1\n" in text and "LINE 19\n" in text
    assert text.count("\n") == 20


def test_multi_file_diff_is_atomic(tmp_path):
    """Second file's context mismatches: the first file must stay untouched."""
    write(tmp_path, "one.py", "a = 1\n")
    write(tmp_path, "two.py", "unexpected content\n")
    diff = (
        "--- a/one.py\n+++ b/one.py\n@@ -1 +1 @@\n-a = 1\n+a = 2\n"
        "--- a/two.py\n+++ b/two.py\n@@ -1 +1 @@\n-b = 1\n+b = 2\n"
    )
    with pytest.raises(PatchError):
        apply_unified_diff(tmp_path, diff)
    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "a = 1\n"


def test_repeated_identical_lines_patch_positionally(tmp_path):
    write(tmp_path, "rep.py", "x = 0\nx = 0\nx = 0\n")
    diff = (
        "--- a/rep.py\n+++ b/rep.py\n"
        "@@ -1,3 +1,3 @@\n"
        " x = 0\n"
        "-x = 0\n"
        "+x = 1\n"
        " x = 0\n"
    )
    apply_unified_diff(tmp_path, diff)
    assert (tmp_path / "rep.py").read_text(encoding="utf-8") == "x = 0\nx = 1\nx = 0\n"


def test_file_without_trailing_newline(tmp_path):
    (tmp_path / "tail.py").write_bytes(b"a = 1\nb = 2")  # no trailing newline
    diff = (
        "--- a/tail.py\n+++ b/tail.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-a = 1\n"
        "+a = 9\n"
        " b = 2\n"
    )
    apply_unified_diff(tmp_path, diff)
    assert (tmp_path / "tail.py").read_text(encoding="utf-8").startswith("a = 9\n")


def test_crlf_file_content_preserved(tmp_path):
    """CRLF input: content must be correct (line endings normalize to LF —
    documented applier behavior)."""
    (tmp_path / "win.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    diff = (
        "--- a/win.py\n+++ b/win.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-a = 1\n"
        "+a = 9\n"
        " b = 2\n"
    )
    apply_unified_diff(tmp_path, diff)
    assert (tmp_path / "win.py").read_text(encoding="utf-8") == "a = 9\nb = 2\n"
