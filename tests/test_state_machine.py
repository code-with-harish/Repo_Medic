import pytest

from repomedic.events import EventBus, ListSink
from repomedic.investigate.state_machine import (
    TRANSITIONS,
    InvalidTransition,
    StateMachine,
)
from repomedic.investigate.state_machine import (
    InvestigationState as S,
)


def make_sm():
    bus = EventBus("s")
    sink = ListSink()
    bus.subscribe(sink)
    return StateMachine(bus), sink


def test_happy_path_transitions():
    sm, sink = make_sm()
    path = [
        S.INGEST, S.GRAPH, S.EXECUTE, S.OBSERVE, S.HYPOTHESIZE, S.RANK,
        S.VERIFY, S.RANK, S.VERIFY, S.ROOT_CAUSE, S.PATCH, S.VALIDATE,
        S.REGRESSION, S.REPORT, S.COMPLETE,
    ]
    for state in path:
        sm.transition(state)
    assert sm.state == S.COMPLETE
    assert len(sink.events) == len(path)
    assert sink.events[0].data["from_state"] == "CREATED"


def test_illegal_transition_raises():
    sm, _ = make_sm()
    sm.transition(S.INGEST)
    with pytest.raises(InvalidTransition):
        sm.transition(S.PATCH)


def test_cannot_patch_without_root_cause():
    """PATCH is only reachable from ROOT_CAUSE anywhere in the table."""
    sources = [src for src, targets in TRANSITIONS.items() if S.PATCH in targets]
    assert sources == [S.ROOT_CAUSE]


def test_no_failure_shortcut():
    sm, _ = make_sm()
    for state in [S.INGEST, S.GRAPH, S.EXECUTE, S.NO_FAILURE, S.REPORT, S.COMPLETE]:
        sm.transition(state)
    assert sm.state == S.COMPLETE


def test_every_state_reaches_terminal():
    """No dead-end non-terminal states in the transition table."""
    terminal = {S.COMPLETE}
    for state, targets in TRANSITIONS.items():
        if state in terminal:
            continue
        assert targets, f"{state} has no outgoing transitions"
