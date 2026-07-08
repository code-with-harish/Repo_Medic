from repomedic.events import EventBus
from repomedic.models.investigation import Evidence, EvidenceKind, InvestigationSession
from repomedic.store.db import SessionStore, SQLiteSink


def make_store(tmp_path):
    return SessionStore(tmp_path / ".repomedic" / "repomedic.db")


def test_session_roundtrip(tmp_path):
    store = make_store(tmp_path)
    sid = store.next_session_id()
    assert sid == "session-001"

    session = InvestigationSession(session_id=sid, repo_path="/repo")
    session.add_evidence(
        Evidence(
            evidence_id="E1",
            kind=EvidenceKind.TEST_FAILURE,
            description="test_x failed",
            data={"test_id": "tests/test_x.py::test_x"},
        )
    )
    store.save_session(session)

    loaded = store.load_session(sid)
    assert loaded is not None
    assert loaded.repo_path == "/repo"
    assert loaded.evidence["E1"].kind == EvidenceKind.TEST_FAILURE

    # update in place
    session.state = "COMPLETE"
    store.save_session(session)
    assert store.load_session(sid).state == "COMPLETE"
    assert store.next_session_id() == "session-002"


def test_event_persistence(tmp_path):
    store = make_store(tmp_path)
    bus = EventBus("session-001")
    bus.subscribe(SQLiteSink(store))
    bus.emit("INGEST", "Repository detected", language="python")
    bus.emit("GRAPH", "42 modules mapped")

    events = store.events_for_session("session-001")
    assert len(events) == 2
    assert events[0]["stage"] == "INGEST"
    assert events[1]["seq"] == 2


def test_list_sessions(tmp_path):
    store = make_store(tmp_path)
    for _ in range(2):
        sid = store.next_session_id()
        store.save_session(InvestigationSession(session_id=sid, repo_path="/r"))
    listed = store.list_sessions()
    assert {s["session_id"] for s in listed} == {"session-001", "session-002"}
