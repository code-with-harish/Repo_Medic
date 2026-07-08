"""SQLite persistence for investigation sessions and their event streams."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from repomedic.events import Event
from repomedic.models.investigation import InvestigationSession

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    data_json TEXT NOT NULL,
    UNIQUE(session_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
"""


class SessionStore:
    """Stores sessions in `<workdir>/.repomedic/repomedic.db` by default."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def next_session_id(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return f"session-{row['n'] + 1:03d}"

    def save_session(self, session: InvestigationSession) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, created_at, repo_path, state, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state = excluded.state,
                    payload_json = excluded.payload_json
                """,
                (
                    session.session_id,
                    session.created_at.isoformat(),
                    session.repo_path,
                    session.state,
                    session.model_dump_json(),
                ),
            )

    def load_session(self, session_id: str) -> InvestigationSession | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        return InvestigationSession.model_validate_json(row["payload_json"])

    def list_sessions(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id, created_at, repo_path, state FROM sessions "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def append_event(self, event: Event) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO events
                    (session_id, seq, timestamp, stage, message, data_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.session_id,
                    event.seq,
                    event.timestamp.isoformat(),
                    event.stage,
                    event.message,
                    event.model_dump_json(include={"data"}),
                ),
            )

    def events_for_session(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT seq, timestamp, stage, message, data_json FROM events "
                "WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]


class SQLiteSink:
    """EventBus sink that persists every event as it is emitted."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    def __call__(self, event: Event) -> None:
        self._store.append_event(event)
