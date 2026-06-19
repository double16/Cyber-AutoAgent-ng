from modules.handlers.events.batch_emitter import BatchingEmitter


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_flush_immediate_emits_single_event_directly():
    base = RecordingEmitter()
    emitter = BatchingEmitter(base, batch_ms=10_000, operation_id="OP_TEST")

    emitter.emit({"type": "output", "content": "one"})
    emitter.flush_immediate()

    assert base.events == [{"type": "output", "content": "one"}]
    assert emitter.batch == []
    assert emitter.timer is None


def test_flush_immediate_wraps_multiple_events_in_batch():
    base = RecordingEmitter()
    emitter = BatchingEmitter(base, batch_ms=10_000, operation_id="OP_TEST")

    first = {"type": "output", "content": "one"}
    second = {"type": "reasoning", "content": "two"}
    emitter.emit(first)
    emitter.emit(second)
    emitter.flush_immediate()

    assert len(base.events) == 1
    assert base.events[0]["type"] == "batch"
    assert base.events[0]["id"].startswith("OP_TEST_batch_")
    assert base.events[0]["events"] == [first, second]


def test_critical_event_flushes_pending_batch_without_deadlock():
    base = RecordingEmitter()
    emitter = BatchingEmitter(base, batch_ms=10_000, operation_id="OP_TEST")

    pending = {"type": "output", "content": "queued"}
    critical = {"type": "error", "content": "stop now"}
    emitter.emit(pending)
    emitter.emit(critical)

    assert base.events == [pending, critical]
    assert emitter.batch == []
    assert emitter.timer is None
