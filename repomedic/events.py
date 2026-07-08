"""Structured event system. Every agent action emits an Event through the bus."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from threading import Lock

from pydantic import BaseModel, Field


class Event(BaseModel):
    """One structured, persistable agent action."""

    seq: int
    session_id: str
    stage: str  # INGEST, GRAPH, EXECUTE, FAILURE, INVESTIGATE, VERIFY, ...
    message: str
    data: dict = Field(default_factory=dict)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )


EventSink = Callable[[Event], None]


class EventBus:
    """Fan-out bus. Sinks: console renderer, SQLite persistence, test capture."""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._sinks: list[EventSink] = []
        self._seq = 0
        self._lock = Lock()

    def subscribe(self, sink: EventSink) -> None:
        self._sinks.append(sink)

    def emit(self, stage: str, message: str, **data: object) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(
                seq=self._seq,
                session_id=self.session_id,
                stage=stage,
                message=message,
                data=dict(data),
            )
        for sink in self._sinks:
            sink(event)
        return event


class ConsoleSink:
    """Renders events as `[STAGE] message` lines.

    STATE transition events are bookkeeping; they are hidden unless verbose.
    """

    def __init__(self, echo: Callable[[str], None] = print, verbose: bool = False) -> None:
        self._echo = echo
        self._verbose = verbose

    def __call__(self, event: Event) -> None:
        if event.stage == "STATE" and not self._verbose:
            return
        self._echo(f"[{event.stage}] {event.message}")


class ListSink:
    """Captures events in memory (used by tests and the report builder)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def __call__(self, event: Event) -> None:
        self.events.append(event)
