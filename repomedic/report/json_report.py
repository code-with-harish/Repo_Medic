"""Render an investigation session as a machine-readable JSON report."""

from __future__ import annotations

import json

from repomedic.events import Event
from repomedic.models.investigation import InvestigationSession


def render(session: InvestigationSession, events: list[Event] | None = None) -> str:
    payload = session.model_dump(mode="json")
    # Trim bulky raw streams from the top-level report; the full session
    # stays available in the SQLite store.
    execution = payload.get("initial_execution")
    if execution and execution.get("command_result"):
        for stream in ("stdout", "stderr"):
            value = execution["command_result"].get(stream, "")
            if len(value) > 8000:
                execution["command_result"][stream] = value[-8000:]
    payload["timeline"] = [
        {
            "seq": event.seq,
            "timestamp": event.timestamp.isoformat(),
            "stage": event.stage,
            "message": event.message,
            "data": event.data,
        }
        for event in (events or [])
    ]
    return json.dumps(payload, indent=2, default=str)
