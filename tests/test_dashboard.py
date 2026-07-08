import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from repomedic.dashboard.app import create_app  # noqa: E402
from repomedic.events import EventBus  # noqa: E402
from repomedic.models.investigation import InvestigationSession  # noqa: E402
from repomedic.store.db import SessionStore, SQLiteSink  # noqa: E402


@pytest.fixture
def client(tmp_path):
    store = SessionStore(tmp_path / "db.sqlite")
    session = InvestigationSession(session_id="session-001", repo_path="/repo",
                                   state="COMPLETE")
    store.save_session(session)
    bus = EventBus("session-001")
    bus.subscribe(SQLiteSink(store))
    bus.emit("INGEST", "Repository detected: Python / pytest")
    return TestClient(create_app(store))


def test_index_serves_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "RepoMedic" in res.text


def test_api_sessions(client):
    res = client.get("/api/sessions")
    assert res.status_code == 200
    assert res.json()[0]["session_id"] == "session-001"


def test_api_session_detail_and_404(client):
    assert client.get("/api/sessions/session-001").json()["state"] == "COMPLETE"
    assert client.get("/api/sessions/nope").status_code == 404


def test_api_events(client):
    events = client.get("/api/sessions/session-001/events").json()
    assert events[0]["stage"] == "INGEST"
    assert client.get("/api/sessions/nope/events").status_code == 404
