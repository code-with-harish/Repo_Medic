"""Minimal read-only web dashboard over the SQLite session store.

Deliberately thin: the CLI is the primary interface; this exists to browse
stored sessions, their event streams and reports.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from repomedic.store.db import SessionStore

INDEX_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>RepoMedic Dashboard</title>
<style>
  body { font-family: ui-monospace, Consolas, monospace; margin: 2rem; background: #111; color: #ddd; }
  h1 { color: #7ee787; }
  table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
  th, td { text-align: left; padding: .4rem .8rem; border-bottom: 1px solid #333; }
  tr:hover { background: #1c1c1c; cursor: pointer; }
  .state-COMPLETE { color: #7ee787; }
  .state-FAILED { color: #ff7b72; }
  .state-NO_FAILURE { color: #79c0ff; }
  pre { background: #1c1c1c; padding: 1rem; overflow-x: auto; }
  #detail { margin-top: 2rem; }
</style>
</head>
<body>
<h1>RepoMedic</h1>
<div id="sessions"></div>
<div id="detail"></div>
<script>
async function load() {
  const res = await fetch('/api/sessions');
  const sessions = await res.json();
  const rows = sessions.map(s =>
    `<tr onclick="show('${s.session_id}')"><td>${s.session_id}</td>` +
    `<td class="state-${s.state}">${s.state}</td>` +
    `<td>${s.created_at}</td><td>${s.repo_path}</td></tr>`).join('');
  document.getElementById('sessions').innerHTML =
    `<table><tr><th>session</th><th>state</th><th>created</th><th>repository</th></tr>${rows}</table>`;
}
async function show(id) {
  const events = await (await fetch(`/api/sessions/${id}/events`)).json();
  const lines = events.map(e => `[${e.stage}] ${e.message}`).join('\\n');
  document.getElementById('detail').innerHTML =
    `<h2>${id}</h2><pre>${lines.replace(/</g, '&lt;')}</pre>`;
}
load();
</script>
</body>
</html>
"""


def create_app(store: SessionStore) -> FastAPI:
    app = FastAPI(title="RepoMedic Dashboard", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/api/sessions")
    def list_sessions() -> list[dict]:
        return store.list_sessions()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = store.load_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/events")
    def get_events(session_id: str) -> list[dict]:
        events = store.events_for_session(session_id)
        if not events:
            raise HTTPException(status_code=404, detail="no events for session")
        return events

    return app
