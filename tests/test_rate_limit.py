from __future__ import annotations

from unittest.mock import Mock

import httpx
import pytest

import modules.rate_limit.rate_limit as rl
from modules.config import types


@pytest.fixture
def inline_to_thread(monkeypatch):
    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(rl.asyncio, "to_thread", fake_to_thread)


# ----------------------------
# _TokenBucket
# ----------------------------

def test_tokenbucket_consume_without_wait(monkeypatch):
    slept = {"called": False}

    def fake_sleep(_):
        slept["called"] = True
        raise AssertionError("sleep should not be called")

    monkeypatch.setattr(rl.time, "sleep", fake_sleep)

    b = rl._TokenBucket(capacity=10.0, refill_rate_per_sec=1.0)
    b.consume_blocking(3.0)

    assert b._tokens == pytest.approx(7.0)
    assert slept["called"] is False


def test_tokenbucket_refill_increases_tokens_up_to_capacity(monkeypatch):
    t = {"now": 1000.0}

    def fake_monotonic():
        return t["now"]

    monkeypatch.setattr(rl.time, "monotonic", fake_monotonic)

    b = rl._TokenBucket(capacity=10.0, refill_rate_per_sec=2.0)  # 2 tokens/sec
    b.consume_blocking(10.0)
    assert b._tokens == pytest.approx(0.0)

    # +3s => +6 tokens
    t["now"] += 3.0
    with b._lock:
        b._refill_locked()
    assert b._tokens == pytest.approx(6.0)

    # Big jump clamps at capacity
    t["now"] += 100.0
    with b._lock:
        b._refill_locked()
    assert b._tokens == pytest.approx(10.0)


def test_tokenbucket_amount_greater_than_capacity_clamps_and_warns(monkeypatch):
    b = rl._TokenBucket(capacity=2.0, refill_rate_per_sec=1.0)
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    b.consume_blocking(10.0)

    assert log.warning.called
    # It clamps to capacity and consumes the whole bucket
    assert b._tokens == pytest.approx(0.0)


def test_tokenbucket_zero_or_negative_is_noop():
    b = rl._TokenBucket(capacity=5.0, refill_rate_per_sec=1.0)
    b.consume_blocking(0)
    b.consume_blocking(-1)
    assert b._tokens == pytest.approx(5.0)


def test_tokenbucket_waits_then_consumes_and_handles_zero_refill(monkeypatch):
    bucket = rl._TokenBucket(capacity=2.0, refill_rate_per_sec=0.0)
    bucket._tokens = 0.0
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        bucket._tokens = 2.0

    monkeypatch.setattr(rl.time, "sleep", sleep)
    bucket.consume_blocking(2.0)

    assert sleeps == [0.5]
    assert bucket._tokens == 0.0


# ----------------------------
# ThreadSafeRateLimiter
# ----------------------------

def test_limiter_init_builds_buckets_and_uses_rpm_per_second_refill():
    cfg = types.RateLimitConfig(rpm=30.0, tpm=600.0, max_concurrent=None)
    limiter = rl.ThreadSafeRateLimiter(cfg)

    assert limiter._req_bucket is not None
    assert limiter._tok_bucket is not None

    assert limiter._req_bucket.capacity == pytest.approx(30.0)
    assert limiter._req_bucket.refill_rate == pytest.approx(30.0 / 60.0)

    assert limiter._tok_bucket.capacity == pytest.approx(600.0)
    assert limiter._tok_bucket.refill_rate == pytest.approx(600.0 / 60.0)


def test_acquire_blocking_calls_buckets_and_returns_release():
    cfg = types.RateLimitConfig(rpm=10.0, tpm=100.0, max_concurrent=1)
    limiter = rl.ThreadSafeRateLimiter(cfg)

    limiter._req_bucket = Mock()
    limiter._tok_bucket = Mock()
    limiter._sem = Mock()

    release = limiter.acquire_blocking(token_cost=55)

    limiter._sem.acquire.assert_called_once()
    limiter._req_bucket.consume_blocking.assert_called_once_with(1.0)
    limiter._tok_bucket.consume_blocking.assert_called_once_with(55.0)

    release()
    limiter._sem.release.assert_called_once()


def test_acquire_blocking_releases_semaphore_on_exception():
    cfg = types.RateLimitConfig(rpm=10.0, tpm=None, max_concurrent=1)
    limiter = rl.ThreadSafeRateLimiter(cfg)

    limiter._req_bucket = Mock()
    limiter._req_bucket.consume_blocking.side_effect = RuntimeError("boom")
    limiter._sem = Mock()

    with pytest.raises(RuntimeError):
        limiter.acquire_blocking(token_cost=0)

    limiter._sem.acquire.assert_called_once()
    limiter._sem.release.assert_called_once()


def test_limiter_cooldown_and_error_classification(monkeypatch):
    cfg = types.RateLimitConfig(rpm=None, tpm=None, max_concurrent=None, cooldown_period=10)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    limiter._cooldown_active = True
    limiter._last_error_time = 100.0
    monkeypatch.setattr(rl.time, "monotonic", lambda: 105.0)
    limiter._cooldown_sem = Mock()

    release = limiter.acquire_blocking(0)
    limiter._cooldown_sem.acquire.assert_called_once()
    release()
    limiter._cooldown_sem.release.assert_called_once()
    assert limiter.report_error("not-a-code") is False
    assert limiter.report_error(999) is False


def test_limiter_cooldown_expiry_error_reporting_and_bucket_cleanup(monkeypatch):
    cfg = types.RateLimitConfig(rpm=10.0, tpm=10.0, max_concurrent=1, cooldown_period=5)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    limiter._cooldown_active = True
    limiter._last_error_time = 10.0
    limiter._cooldown_sem = Mock()
    limiter._sem = Mock()
    limiter._req_bucket = Mock()
    limiter._tok_bucket = Mock()
    monkeypatch.setattr(rl.time, "monotonic", lambda: 16.0)

    release = limiter.acquire_blocking(0)

    assert limiter._cooldown_active is False
    limiter._cooldown_sem.acquire.assert_not_called()
    limiter._sem.acquire.assert_called_once()
    limiter._tok_bucket.consume_blocking.assert_not_called()
    release()
    limiter._sem.release.assert_called_once()

    assert limiter.report_error(None) is False
    assert limiter.report_error("429") is True
    assert limiter._cooldown_active is True
    assert limiter.report_error(429) is True

    limiter._cooldown_active = True
    limiter._req_bucket.consume_blocking.side_effect = RuntimeError("bucket unavailable")
    limiter._last_error_time = 15.0
    monkeypatch.setattr(rl.time, "monotonic", lambda: 16.0)
    with pytest.raises(RuntimeError, match="bucket unavailable"):
        limiter.acquire_blocking(1)
    assert limiter._cooldown_sem.release.called


def test_handle_exception_supports_response_codes_and_retryable_error(monkeypatch):
    limiter = rl.ThreadSafeRateLimiter(
        types.RateLimitConfig(max_retries=2, retry_base_delay=1.0, retry_max_delay=1.5)
    )
    monkeypatch.setattr(rl.time, "sleep", lambda _delay: None)

    response_error = type("ResponseError", (Exception,), {"response": type("Response", (), {"status_code": 429})()})()
    assert limiter._handle_exception(response_error, 1) == 1.5

    assert limiter._handle_exception(rl._RetryableError(503), 0) == 1.0
    with pytest.raises(RuntimeError, match="Response error 503"):
        limiter._handle_exception(rl._RetryableError(503), 2)


def test_batch_message_conversion_ignores_invalid_shapes_and_preserves_message_metadata():
    assert rl._batch_messages_to_strands_messages(None) == []
    assert rl._batch_messages_to_strands_messages(["not a conversation"]) == [{}]

    class BareMessage:
        content = None
        additional_kwargs = {"ignored": True}

    converted = rl._batch_messages_to_strands_messages([
        [_Msg("text", {"tool": "call"}), BareMessage()],
        "invalid conversation",
    ])

    assert converted == [{"content": "text", "json": {"tool": "call"}}, {"json": {"ignored": True}}]


# ----------------------------
# strands patching
# ----------------------------

@pytest.mark.asyncio
async def test_patch_and_unpatch_stream(monkeypatch, inline_to_thread):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    events = [{"e": 1}, {"e": 2}]

    class DummyModel:
        async def stream(
                self,
                messages,
                tool_specs=None,
                system_prompt=None,
                *,
                tool_choice=None,
                system_prompt_content=None,
                **kwargs,
        ):
            for e in events:
                yield e

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, tpm=None, max_concurrent=None, assume_output_tokens=0)

    released = {"count": 0}

    def release():
        released["count"] += 1

    limiter.acquire_blocking.return_value = release

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        assert hasattr(DummyModel, rl._ORIG_STREAM_ATTR)

        m = DummyModel()
        got = []
        async for e in m.stream(messages=[{"role": "user", "content": [{"text": "hello"}]}]):
            got.append(e)

        assert got == events
        assert limiter.acquire_blocking.call_count == 1
        assert released["count"] == 1

        orig = getattr(DummyModel, rl._ORIG_STREAM_ATTR)
        rl.unpatch_model_provider_class(DummyModel)
        assert not hasattr(DummyModel, rl._ORIG_STREAM_ATTR)
        assert DummyModel.stream is orig
    finally:
        rl.unpatch_model_provider_class(DummyModel)


@pytest.mark.asyncio
async def test_stream_retries_read_timeout_and_emits_reason(monkeypatch, inline_to_thread, capsys):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", no_sleep)
    cfg = types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    calls = {"count": 0}

    class DummyModel:
        async def stream(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadTimeout("model response timed out")
            yield {"ok": True}

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        events = [event async for event in DummyModel().stream(messages=[])]
    finally:
        rl.unpatch_model_provider_class(DummyModel)

    assert events == [{"ok": True}]
    assert calls["count"] == 2
    assert '"reason": "read_timeout"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_stream_does_not_retry_unrelated_exception(monkeypatch, inline_to_thread):
    cfg = types.RateLimitConfig(max_retries=3, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    calls = {"count": 0}

    class DummyModel:
        async def stream(self, *args, **kwargs):
            calls["count"] += 1
            raise ValueError("invalid model response")
            yield  # pragma: no cover

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        with pytest.raises(ValueError, match="invalid model response"):
            _ = [event async for event in DummyModel().stream(messages=[])]
    finally:
        rl.unpatch_model_provider_class(DummyModel)

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_stream_error_event_retries_and_structured_error_event_is_retried(monkeypatch, inline_to_thread):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", no_sleep)
    cfg = types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    stream_calls = {"count": 0}
    structured_calls = {"count": 0}

    class DummyModel:
        async def stream(self, *args, **kwargs):
            stream_calls["count"] += 1
            if stream_calls["count"] == 1:
                yield {"type": "error", "code": 429}
            else:
                yield {"ok": "stream"}

        async def structured_output(self, *args, **kwargs):
            structured_calls["count"] += 1
            if structured_calls["count"] == 1:
                yield {"type": "error", "code": 503}
            else:
                yield {"ok": "structured"}

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        assert [event async for event in DummyModel().stream(messages=[])] == [{"ok": "stream"}]
        assert [event async for event in DummyModel().structured_output(dict, prompt=[])] == [{"ok": "structured"}]
    finally:
        rl.unpatch_model_provider_class(DummyModel)

    assert stream_calls["count"] == 2
    assert structured_calls["count"] == 2


def test_generate_retries_read_timeout(monkeypatch):
    monkeypatch.setattr(rl.time, "sleep", lambda _delay: None)
    cfg = types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    calls = {"count": 0}

    class DummyLC:
        def generate(self, messages, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadTimeout("model response timed out")
            return {"ok": True}

    try:
        rl.patch_langchain_chat_class_generate(DummyLC, limiter)
        assert DummyLC().generate([]) == {"ok": True}
    finally:
        rl.unpatch_langchain_chat_class_generate(DummyLC)

    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_agenerate_retries_read_timeout(monkeypatch, inline_to_thread):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", no_sleep)
    cfg = types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    calls = {"count": 0}

    class DummyLC:
        async def agenerate(self, messages, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadTimeout("model response timed out")
            return {"ok": True}

    try:
        rl.patch_langchain_chat_class_generate(DummyLC, limiter)
        assert await DummyLC().agenerate([]) == {"ok": True}
    finally:
        rl.unpatch_langchain_chat_class_generate(DummyLC)

    assert calls["count"] == 2


def test_read_timeout_is_raised_after_retry_budget(monkeypatch):
    monkeypatch.setattr(rl.time, "sleep", lambda _delay: None)
    limiter = rl.ThreadSafeRateLimiter(
        types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    )

    with pytest.raises(httpx.ReadTimeout):
        limiter.handle_exception(httpx.ReadTimeout("model response timed out"), attempt=1)


@pytest.mark.asyncio
async def test_structured_output_retries_read_timeout(monkeypatch, inline_to_thread):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(rl.asyncio, "sleep", no_sleep)
    cfg = types.RateLimitConfig(max_retries=1, retry_base_delay=0.0, retry_max_delay=0.0)
    limiter = rl.ThreadSafeRateLimiter(cfg)
    calls = {"count": 0}

    class DummyModel:
        async def stream(self, *args, **kwargs):
            yield {"stream": True}

        async def structured_output(self, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ReadTimeout("model response timed out")
            yield {"ok": True}

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        events = [event async for event in DummyModel().structured_output(dict, prompt=[])]
    finally:
        rl.unpatch_model_provider_class(DummyModel)

    assert events == [{"ok": True}]
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_patch_structured_output(monkeypatch, inline_to_thread):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    class DummyModel:
        async def stream(self, *args, **kwargs):
            yield {"stream": True}

        async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
            yield {"ok": True}

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, tpm=None, max_concurrent=None, assume_output_tokens=0)

    released = {"count": 0}

    def release():
        released["count"] += 1

    limiter.acquire_blocking.return_value = release

    try:
        rl.patch_model_provider_class(DummyModel, limiter)
        assert hasattr(DummyModel, rl._ORIG_STRUCT_ATTR)

        m = DummyModel()
        got = []
        async for e in m.structured_output(dict, prompt=[{"role": "user", "content": [{"json": {"a": 1}}]}]):
            got.append(e)

        assert got == [{"ok": True}]
        assert limiter.acquire_blocking.call_count >= 1
        assert released["count"] == 1
    finally:
        rl.unpatch_model_provider_class(DummyModel)


def test_patch_model_provider_class_no_stream_is_noop(monkeypatch):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    class NoStream:
        pass

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0)

    rl.patch_model_provider_class(NoStream, limiter)
    assert not hasattr(NoStream, rl._ORIG_STREAM_ATTR)
    assert log.warning.called

# ----------------------------
# langchain patching
# ----------------------------


class _Msg:
    """Minimal BaseMessage-like object for LangChain tests."""
    def __init__(self, content, additional_kwargs=None):
        self.content = content
        self.additional_kwargs = additional_kwargs or {}


def test_patch_generate_calls_limiter_and_releases(monkeypatch):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    calls = {"release": 0}

    def release():
        calls["release"] += 1

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, tpm=None, max_concurrent=None, assume_output_tokens=0)
    limiter.acquire_blocking.return_value = release

    class DummyLC:
        def generate(self, messages, *args, **kwargs):
            return {"ok": True, "messages": messages, "kwargs": kwargs}

    orig_generate = DummyLC.generate

    try:
        rl.patch_langchain_chat_class_generate(DummyLC, limiter)
        assert hasattr(DummyLC, rl._ORIG_GENERATE_ATTR)
        assert getattr(DummyLC, rl._ORIG_GENERATE_ATTR) is orig_generate
        assert DummyLC.generate is not orig_generate

        messages = [[
            _Msg("abcd"),
            _Msg([{"json": {"a": 1}}]),
            _Msg("x", additional_kwargs={"tool_calls": [{"id": "1", "args": {"k": "v"}}]}),
        ]]

        out = DummyLC().generate(messages, temperature=0.1)

        assert out["ok"] is True
        assert out["messages"] == messages
        assert out["kwargs"]["temperature"] == 0.1

        # limiter invoked once, release called once
        assert limiter.acquire_blocking.call_count == 1
        assert calls["release"] == 1
    finally:
        rl.unpatch_langchain_chat_class_generate(DummyLC)
        assert not hasattr(DummyLC, rl._ORIG_GENERATE_ATTR)
        assert DummyLC.generate is orig_generate


@pytest.mark.asyncio
async def test_patch_agenerate_calls_limiter_and_releases(monkeypatch, inline_to_thread):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    calls = {"release": 0}

    def release():
        calls["release"] += 1

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, tpm=None, max_concurrent=None, assume_output_tokens=0)
    limiter.acquire_blocking.return_value = release

    class DummyLC:
        async def agenerate(self, messages, *args, **kwargs):
            return {"ok": True, "messages": messages, "kwargs": kwargs}

    orig_agenerate = DummyLC.agenerate

    try:
        rl.patch_langchain_chat_class_generate(DummyLC, limiter)
        assert hasattr(DummyLC, rl._ORIG_AGENERATE_ATTR)
        assert getattr(DummyLC, rl._ORIG_AGENERATE_ATTR) is orig_agenerate
        assert DummyLC.agenerate is not orig_agenerate

        messages = [[
            _Msg("hello"),
            _Msg({"json": {"b": 2}}),
        ]]

        out = await DummyLC().agenerate(messages, max_tokens=123)

        assert out["ok"] is True
        assert out["messages"] == messages
        assert out["kwargs"]["max_tokens"] == 123

        assert limiter.acquire_blocking.call_count == 1
        assert calls["release"] == 1
    finally:
        rl.unpatch_langchain_chat_class_generate(DummyLC)
        assert not hasattr(DummyLC, rl._ORIG_AGENERATE_ATTR)
        assert DummyLC.agenerate is orig_agenerate


@pytest.mark.asyncio
async def test_agenerate_accepts_a_synchronous_return_value(monkeypatch, inline_to_thread):
    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, assume_output_tokens=0)
    limiter.acquire_blocking.return_value = lambda: None

    class SyncResultLC:
        def agenerate(self, messages, *args, **kwargs):
            return {"messages": messages}

    try:
        rl.patch_langchain_chat_class_generate(SyncResultLC, limiter)
        assert await SyncResultLC().agenerate([]) == {"messages": []}
    finally:
        rl.unpatch_langchain_chat_class_generate(SyncResultLC)


def test_unpatch_is_idempotent(monkeypatch):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    class DummyLC:
        def generate(self, messages, *args, **kwargs):
            return "gen"

        async def agenerate(self, messages, *args, **kwargs):
            return "agen"

    # Unpatch before patch should not raise
    rl.unpatch_langchain_chat_class_generate(DummyLC)

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0, assume_output_tokens=0)
    limiter.acquire_blocking.return_value = lambda: None

    try:
        rl.patch_langchain_chat_class_generate(DummyLC, limiter)
    finally:
        # Unpatch twice should not raise
        rl.unpatch_langchain_chat_class_generate(DummyLC)
        rl.unpatch_langchain_chat_class_generate(DummyLC)


def test_patch_missing_methods_is_noop(monkeypatch):
    log = Mock()
    monkeypatch.setattr(rl, "logger", log)

    class NoGenerate:
        pass

    limiter = Mock(spec=rl.ThreadSafeRateLimiter)
    limiter.cfg = types.RateLimitConfig(rpm=10.0)

    rl.patch_langchain_chat_class_generate(NoGenerate, limiter)

    assert not hasattr(NoGenerate, rl._ORIG_GENERATE_ATTR)
    assert not hasattr(NoGenerate, rl._ORIG_AGENERATE_ATTR)
    assert log.warning.called
