"""Investigation engine: drives the state machine end to end.

INGEST -> GRAPH -> EXECUTE -> OBSERVE -> HYPOTHESIZE -> RANK -> (VERIFY ->
RANK)* -> ROOT_CAUSE -> PATCH -> VALIDATE -> REGRESSION -> REPORT.

Invariants enforced here:
- source files of the target repository are never executed or modified in
  place (all execution happens in Workspace copies; the only writes into the
  target are session artifacts under `.repomedic/`);
- a root cause is only declared with recorded supporting evidence;
- a patch is only `accepted` when validation commands actually ran and passed.
"""

from __future__ import annotations

from pathlib import Path

from repomedic.events import EventBus, ListSink
from repomedic.execute.base import DEFAULT_TIMEOUT_S, Executor, Workspace
from repomedic.execute.parser import run_pytest
from repomedic.ingest.detector import detect
from repomedic.ingest.graph import build_repo_model
from repomedic.investigate.experiments import run_experiment, update_confidence
from repomedic.investigate.heuristics import _pytest_node_id
from repomedic.investigate.provider import AgentProvider
from repomedic.investigate.state_machine import InvestigationState as S
from repomedic.investigate.state_machine import StateMachine
from repomedic.models.investigation import (
    Evidence,
    EvidenceKind,
    HypothesisStatus,
    InvestigationSession,
    RootCause,
)
from repomedic.patch.validator import validate_patch
from repomedic.report import json_report, markdown

ROOT_CAUSE_THRESHOLD = 0.60
MAX_EXPERIMENTS = 4


class InvestigationEngine:
    def __init__(
        self,
        repo_path: str | Path,
        provider: AgentProvider,
        executor: Executor,
        bus: EventBus,
        session: InvestigationSession,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        reports_dir: Path | None = None,
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.provider = provider
        self.executor = executor
        self.bus = bus
        self.session = session
        self.timeout_s = timeout_s
        self.reports_dir = reports_dir or (self.repo_path / ".repomedic" / "reports")
        self.sm = StateMachine(bus)
        self._workspace: Workspace | None = None
        self._failing_node_ids: list[str] = []
        # Engine keeps its own event capture for the report timeline.
        self._timeline = ListSink()
        bus.subscribe(self._timeline)

    # ------------------------------------------------------------------ #

    def run(self) -> InvestigationSession:
        try:
            self._run_pipeline()
        except Exception as exc:  # noqa: BLE001 - engine converts to FAILED state
            self.session.error = f"{type(exc).__name__}: {exc}"
            if self.sm.can_transition(S.FAILED):
                self.sm.transition(S.FAILED)
                self.bus.emit("ERROR", self.session.error)
            self._report()
        finally:
            if self._workspace is not None:
                self._workspace.cleanup()
            if self.session.report_markdown_path is None:
                # _report never ran (e.g. report write failed): keep raw FSM state.
                self.session.state = self.sm.state.value
        return self.session

    def _run_pipeline(self) -> None:
        if not self._ingest():
            return
        self._graph()
        if not self._execute():
            self._report()
            return
        self._observe()
        self._hypothesize()
        if not self._verify_loop():
            self._report()
            return
        if not self._patch_and_validate():
            self._report()
            return
        self._report()

    # ------------------------------------------------------------------ #
    # stages
    # ------------------------------------------------------------------ #

    def _ingest(self) -> bool:
        self.sm.transition(S.INGEST)
        detection = detect(self.repo_path)
        if detection.language != "python" or not detection.test_command:
            self.bus.emit(
                "INGEST",
                f"Unsupported repository (language={detection.language}, "
                f"test_framework={detection.test_framework})",
            )
            self.session.error = "unsupported repository"
            self.sm.transition(S.FAILED)
            self._report()
            return False
        self._detection = detection
        self.bus.emit(
            "INGEST",
            f"Repository detected: Python / {detection.test_framework}",
            language=detection.language,
            package_manager=detection.package_manager,
            test_framework=detection.test_framework,
        )
        return True

    def _graph(self) -> None:
        self.sm.transition(S.GRAPH)
        repo = build_repo_model(self.repo_path, self._detection)
        self.session.repo = repo
        self.bus.emit(
            "GRAPH",
            f"{repo.module_count} modules mapped",
            modules=repo.module_count,
            edges=len(repo.edges),
        )

    def _execute(self) -> bool:
        """Returns False when there is nothing to investigate."""
        self.sm.transition(S.EXECUTE)
        self.bus.emit("EXECUTE", f"Running {self._detection.test_framework}",
                      executor=self.executor.name)
        self._workspace = Workspace(self.repo_path, label="run")
        execution = run_pytest(
            self.executor, self._workspace.path,
            base_command=self._detection.test_command, timeout_s=self.timeout_s,
        )
        self.session.initial_execution = execution
        if execution.all_green:
            self.bus.emit("EXECUTE", f"{execution.total} tests: all passing",
                          total=execution.total)
            self.sm.transition(S.NO_FAILURE)
            return False
        failing = execution.failed + execution.errors
        if failing:
            self.bus.emit("FAILURE", f"{failing} failing tests detected",
                          failed=execution.failed, errors=execution.errors,
                          passed=execution.passed)
        else:
            # Non-zero exit but nothing parseable (e.g. crash before pytest
            # could write results): report that honestly, don't invent counts.
            self.bus.emit(
                "FAILURE",
                f"test command exited {execution.command_result.exit_code} "
                f"with no parseable test results",
                exit_code=execution.command_result.exit_code,
            )
        return True

    def _observe(self) -> None:
        self.sm.transition(S.OBSERVE)
        execution = self.session.initial_execution
        repo = self.session.repo
        for failure in execution.failures:
            eid = self.session.next_evidence_id()
            self.session.add_evidence(Evidence(
                evidence_id=eid,
                kind=EvidenceKind.TEST_FAILURE,
                description=f"{failure.test_id} failed: "
                            f"{failure.exception_type or 'failure'}: {failure.message}",
                data={"test_id": failure.test_id, "raw": failure.raw[-4000:]},
            ))
            frame = failure.deepest_repo_frame()
            if frame:
                eid = self.session.next_evidence_id()
                self.session.add_evidence(Evidence(
                    evidence_id=eid,
                    kind=EvidenceKind.TRACEBACK,
                    description=f"traceback of {failure.test_id} ends in repo at "
                                f"{frame.file}:{frame.line} (`{frame.function}`)",
                    data={"file": frame.file, "line": frame.line},
                ))
            node_id = _pytest_node_id(failure, repo)
            if node_id:
                self._failing_node_ids.append(node_id)

    def _hypothesize(self) -> None:
        self.sm.transition(S.HYPOTHESIZE)
        repo = self.session.repo
        hypotheses = self.provider.generate_hypotheses(repo, self.session.initial_execution)
        # Convert each provider-reported static finding into recorded evidence.
        for hyp in hypotheses:
            for finding in hyp.patch_context.pop("static_findings", []):
                eid = self.session.next_evidence_id()
                self.session.add_evidence(Evidence(
                    evidence_id=eid,
                    kind=EvidenceKind.STATIC_ANALYSIS,
                    description=finding,
                    data={"hypothesis": hyp.hypothesis_id},
                ))
                hyp.supporting_evidence.append(eid)
        self.session.hypotheses = hypotheses
        self.bus.emit("INVESTIGATE", f"Generated {len(hypotheses)} hypotheses",
                      provider=self.provider.name,
                      hypotheses=[h.hypothesis_id for h in hypotheses])

    def _verify_loop(self) -> bool:
        """RANK -> VERIFY loop. Returns True when a root cause was selected."""
        experiments_run = 0
        while True:
            self.sm.transition(S.RANK)
            ranked = sorted(
                (h for h in self.session.hypotheses
                 if h.status != HypothesisStatus.REJECTED),
                key=lambda h: h.confidence, reverse=True,
            )
            if not ranked:
                message = ("No hypotheses generated; inconclusive"
                           if not self.session.hypotheses
                           else "All hypotheses rejected; inconclusive")
                self.bus.emit("INVESTIGATE", message)
                self.sm.transition(S.FAILED)
                return False

            candidate = next(
                (h for h in ranked
                 if h.experiment and h.experiment.status == "proposed"),
                None,
            )
            if candidate is not None and experiments_run < MAX_EXPERIMENTS:
                self.sm.transition(S.VERIFY)
                self.bus.emit("VERIFY", f"Testing hypothesis {candidate.hypothesis_id}",
                              hypothesis=candidate.hypothesis_id,
                              experiment=candidate.experiment.description)
                verdict = run_experiment(
                    self.executor, self._workspace.path, candidate.experiment,
                    timeout_s=self.timeout_s,
                )
                experiments_run += 1
                before, after = update_confidence(candidate, verdict)
                eid = self.session.next_evidence_id()
                self.session.add_evidence(Evidence(
                    evidence_id=eid,
                    kind=EvidenceKind.EXPERIMENT,
                    description=f"experiment for {candidate.hypothesis_id} "
                                f"({candidate.experiment.description}): {verdict}",
                    data={
                        "verdict": verdict,
                        "exit_code": candidate.experiment.command_result.exit_code
                        if candidate.experiment.command_result else None,
                    },
                ))
                if verdict == "supports":
                    candidate.supporting_evidence.append(eid)
                elif verdict == "contradicts":
                    candidate.contradicting_evidence.append(eid)
                self.bus.emit(
                    "VERIFY",
                    f"{candidate.hypothesis_id} confidence {before:.2f} -> {after:.2f}",
                    hypothesis=candidate.hypothesis_id, verdict=verdict,
                    before=before, after=after,
                )
                continue

            # No more experiments to run: decide.
            top = ranked[0]
            if top.confidence >= ROOT_CAUSE_THRESHOLD and top.supporting_evidence:
                self.sm.transition(S.ROOT_CAUSE)
                suspect = top.suspect
                location = f"{suspect.file}:{suspect.line}" if suspect else "unknown"
                self.session.root_cause = RootCause(
                    hypothesis_id=top.hypothesis_id,
                    description=top.description,
                    file=suspect.file if suspect else "",
                    line=suspect.line if suspect else 0,
                    confidence=top.confidence,
                    evidence_ids=list(top.supporting_evidence),
                )
                self.bus.emit("ROOT_CAUSE", location,
                              hypothesis=top.hypothesis_id,
                              confidence=top.confidence,
                              description=top.description)
                return True
            self.bus.emit(
                "INVESTIGATE",
                f"No hypothesis reached confidence {ROOT_CAUSE_THRESHOLD:.2f} "
                f"with recorded evidence; inconclusive",
                top_hypothesis=top.hypothesis_id, top_confidence=top.confidence,
            )
            self.sm.transition(S.FAILED)
            return False

    def _patch_and_validate(self) -> bool:
        self.sm.transition(S.PATCH)
        root_hyp = self.session.hypothesis(self.session.root_cause.hypothesis_id)
        patch = self.provider.propose_patch(self.session.repo, root_hyp)
        if patch is None or not patch.diff.strip():
            self.bus.emit("PATCH", "No patch template available for this root cause")
            return False
        self.session.patch = patch
        self.bus.emit("PATCH", f"Generated {patch.lines_changed}-line patch",
                      files=patch.files, description=patch.description)

        self.sm.transition(S.VALIDATE)
        validation = validate_patch(
            self.repo_path, patch, self._failing_node_ids,
            self._detection.test_command, self.executor, timeout_s=self.timeout_s,
        )
        self.session.validation = validation
        eid = self.session.next_evidence_id()
        self.session.add_evidence(Evidence(
            evidence_id=eid,
            kind=EvidenceKind.VALIDATION,
            description=f"patch validation verdict: {validation.verdict}",
            data={"verdict": validation.verdict},
        ))
        original_status = "PASS" if validation.original_failures_passed else "FAIL"
        self.bus.emit("VALIDATE", f"Original failures: {original_status}",
                      passed=validation.original_failures_passed)
        if not validation.original_failures_passed:
            self.bus.emit("VALIDATE", "Patch rejected: original failures persist")
            return False

        self.sm.transition(S.REGRESSION)
        regression_status = "PASS" if validation.regression_passed else "FAIL"
        self.bus.emit("REGRESSION",
                      f"{validation.regression_total} tests: {regression_status}",
                      total=validation.regression_total,
                      passed=validation.regression_passed)
        if not validation.regression_passed:
            self.bus.emit("REGRESSION", "Patch rejected: regression suite failed")
            return False
        return True

    def _report(self) -> None:
        if self.sm.can_transition(S.REPORT):
            self.sm.transition(S.REPORT)
        # Resolve the meaningful terminal state before rendering so the report
        # shows it (the FSM itself still finishes via REPORT -> COMPLETE).
        if S.FAILED in self.sm.history:
            self.session.state = S.FAILED.value
        elif S.NO_FAILURE in self.sm.history:
            self.session.state = S.NO_FAILURE.value
        else:
            self.session.state = S.COMPLETE.value
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        md_path = self.reports_dir / f"{self.session.session_id}.md"
        json_path = self.reports_dir / f"{self.session.session_id}.json"
        events = self._timeline.events
        md_path.write_text(markdown.render(self.session, events), encoding="utf-8")
        json_path.write_text(json_report.render(self.session, events), encoding="utf-8")
        self.session.report_markdown_path = str(md_path)
        self.session.report_json_path = str(json_path)
        try:
            display = md_path.relative_to(self.repo_path).as_posix()
        except ValueError:
            display = str(md_path)
        self.bus.emit("REPORT", display, markdown=str(md_path), json=str(json_path))
        if self.sm.can_transition(S.COMPLETE):
            self.sm.transition(S.COMPLETE)
