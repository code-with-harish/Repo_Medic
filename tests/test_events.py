from repomedic.events import ConsoleSink, EventBus, ListSink


def test_emit_increments_seq_and_fans_out():
    bus = EventBus(session_id="session-001")
    sink_a, sink_b = ListSink(), ListSink()
    bus.subscribe(sink_a)
    bus.subscribe(sink_b)

    bus.emit("INGEST", "Repository detected: Python / pytest", language="python")
    bus.emit("GRAPH", "3 modules mapped", modules=3)

    assert [e.seq for e in sink_a.events] == [1, 2]
    assert sink_a.events[0].stage == "INGEST"
    assert sink_a.events[0].data == {"language": "python"}
    assert sink_a.events[0].session_id == "session-001"
    assert len(sink_b.events) == 2


def test_console_sink_format():
    lines: list[str] = []
    bus = EventBus("s")
    bus.subscribe(ConsoleSink(echo=lines.append))
    bus.emit("EXECUTE", "Running pytest")
    assert lines == ["[EXECUTE] Running pytest"]
