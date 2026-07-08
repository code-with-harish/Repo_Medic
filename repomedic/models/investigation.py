"""Investigation state: hypotheses, evidence, experiments, patches, validation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from repomedic.models.execution import CommandResult, ExecutionResult
from repomedic.models.repo import RepoModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class EvidenceKind(StrEnum):
    TEST_FAILURE = "test_failure"
    TRACEBACK = "traceback"
    STATIC_ANALYSIS = "static_analysis"
    EXPERIMENT = "experiment"
    VALIDATION = "validation"


class Evidence(BaseModel):
    """A recorded, reproducible observation. Root causes require these."""

    evidence_id: str
    kind: EvidenceKind
    description: str
    data: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    RAN = "ran"
    SKIPPED = "skipped"


class Experiment(BaseModel):
    """A safe, isolated verification experiment for one hypothesis.

    Two shapes:
    - script experiments: a standalone Python snippet run inside the isolated
      workspace; it must print VERDICT:SUPPORTS / VERDICT:CONTRADICTS /
      VERDICT:INCONCLUSIVE.
    - command experiments: an arbitrary command (e.g. re-running one failing
      test in isolation); exit code 0 maps to `supports` when
      `supports_on_exit_zero` is true, otherwise to `contradicts`.
    """

    experiment_id: str
    description: str
    script: str | None = None
    command: list[str] | None = None
    supports_on_exit_zero: bool = True
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    verdict: str | None = None  # "supports" | "contradicts" | "inconclusive" | None
    command_result: CommandResult | None = None


class HypothesisStatus(StrEnum):
    OPEN = "open"
    VERIFIED = "verified"
    REJECTED = "rejected"


class SuspectLocation(BaseModel):
    file: str
    line: int
    symbol: str = ""


class Hypothesis(BaseModel):
    """One candidate root cause with an evidence-backed confidence score."""

    hypothesis_id: str  # H1, H2, ...
    description: str
    category: str  # e.g. "mutable_shared_state", "schema_mismatch"
    confidence: float = 0.0
    prior: float = 0.0
    supporting_evidence: list[str] = Field(default_factory=list)  # evidence ids
    contradicting_evidence: list[str] = Field(default_factory=list)
    experiment: Experiment | None = None
    suspect: SuspectLocation | None = None
    status: HypothesisStatus = HypothesisStatus.OPEN
    # Opaque payload the strategy that produced this hypothesis uses to build a patch.
    patch_context: dict = Field(default_factory=dict)


class RootCause(BaseModel):
    hypothesis_id: str
    description: str
    file: str
    line: int
    confidence: float
    evidence_ids: list[str]


class PatchProposal(BaseModel):
    """A minimal unified diff plus metadata."""

    diff: str
    files: list[str] = Field(default_factory=list)
    description: str = ""
    lines_changed: int = 0


class ValidationResult(BaseModel):
    """Outcome of applying the patch and re-running tests. Only trusted if commands ran."""

    original_failures_passed: bool = False
    regression_passed: bool = False
    regression_total: int = 0
    original_run: ExecutionResult | None = None
    regression_run: ExecutionResult | None = None
    verdict: str = "not_run"  # "accepted" | "rejected_original" | "rejected_regression" | "not_run"


class InvestigationSession(BaseModel):
    """The complete persisted state of one investigation."""

    session_id: str
    repo_path: str
    created_at: datetime = Field(default_factory=utcnow)
    state: str = "CREATED"
    executor: str = "local"
    repo: RepoModel | None = None
    initial_execution: ExecutionResult | None = None
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    root_cause: RootCause | None = None
    patch: PatchProposal | None = None
    validation: ValidationResult | None = None
    report_markdown_path: str | None = None
    report_json_path: str | None = None
    error: str | None = None

    def add_evidence(self, evidence: Evidence) -> str:
        self.evidence[evidence.evidence_id] = evidence
        return evidence.evidence_id

    def next_evidence_id(self) -> str:
        return f"E{len(self.evidence) + 1}"

    def hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        for hyp in self.hypotheses:
            if hyp.hypothesis_id == hypothesis_id:
                return hyp
        return None
