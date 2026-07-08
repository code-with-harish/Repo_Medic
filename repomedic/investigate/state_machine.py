"""Explicit investigation state machine.

Transitions are declared in a table and enforced at runtime; every transition
emits a structured event. The engine cannot, for example, jump to PATCH
without passing through ROOT_CAUSE.
"""

from __future__ import annotations

from enum import StrEnum

from repomedic.events import EventBus


class InvestigationState(StrEnum):
    CREATED = "CREATED"
    INGEST = "INGEST"
    GRAPH = "GRAPH"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    HYPOTHESIZE = "HYPOTHESIZE"
    RANK = "RANK"
    VERIFY = "VERIFY"
    ROOT_CAUSE = "ROOT_CAUSE"
    PATCH = "PATCH"
    VALIDATE = "VALIDATE"
    REGRESSION = "REGRESSION"
    REPORT = "REPORT"
    COMPLETE = "COMPLETE"
    NO_FAILURE = "NO_FAILURE"  # tests already pass; nothing to investigate
    FAILED = "FAILED"  # investigation could not conclude


S = InvestigationState

TRANSITIONS: dict[InvestigationState, frozenset[InvestigationState]] = {
    S.CREATED: frozenset({S.INGEST, S.FAILED}),
    S.INGEST: frozenset({S.GRAPH, S.FAILED}),
    S.GRAPH: frozenset({S.EXECUTE, S.FAILED}),
    S.EXECUTE: frozenset({S.OBSERVE, S.NO_FAILURE, S.FAILED}),
    S.OBSERVE: frozenset({S.HYPOTHESIZE, S.FAILED}),
    S.HYPOTHESIZE: frozenset({S.RANK, S.FAILED}),
    S.RANK: frozenset({S.VERIFY, S.ROOT_CAUSE, S.FAILED}),
    # VERIFY loops back to RANK after each experiment (confidence update).
    S.VERIFY: frozenset({S.RANK, S.ROOT_CAUSE, S.FAILED}),
    S.ROOT_CAUSE: frozenset({S.PATCH, S.REPORT, S.FAILED}),
    S.PATCH: frozenset({S.VALIDATE, S.REPORT, S.FAILED}),
    S.VALIDATE: frozenset({S.REGRESSION, S.REPORT, S.FAILED}),
    S.REGRESSION: frozenset({S.REPORT, S.FAILED}),
    S.REPORT: frozenset({S.COMPLETE}),
    S.NO_FAILURE: frozenset({S.REPORT}),
    S.COMPLETE: frozenset(),
    S.FAILED: frozenset({S.REPORT}),
}


class InvalidTransition(RuntimeError):
    pass


class StateMachine:
    def __init__(self, bus: EventBus, initial: InvestigationState = S.CREATED) -> None:
        self._bus = bus
        self.state = initial
        self.history: list[InvestigationState] = [initial]

    def transition(self, to: InvestigationState, message: str = "", **data: object) -> None:
        allowed = TRANSITIONS[self.state]
        if to not in allowed:
            raise InvalidTransition(
                f"illegal transition {self.state.value} -> {to.value}; "
                f"allowed: {sorted(s.value for s in allowed)}"
            )
        previous = self.state
        self.state = to
        self.history.append(to)
        self._bus.emit(
            "STATE",
            message or f"{previous.value} -> {to.value}",
            from_state=previous.value,
            to_state=to.value,
            **data,
        )

    def can_transition(self, to: InvestigationState) -> bool:
        return to in TRANSITIONS[self.state]
