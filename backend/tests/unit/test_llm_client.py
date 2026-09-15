"""Phase 1 测试：core/llm_client.py（用假 provider 与内存 sink，不触网）。"""

import json

import pytest

from src.core.llm_client import LLMClient, OpenAICompatibleProvider


class FakeSink:
    def __init__(self):
        self.records = []

    def record(self, rec):
        self.records.append(rec)


class FakeProvider:
    """脚本化 provider：acomplete 按序返回响应；astream 按序吐词。"""

    def __init__(self, responses=None, stream=None, fail=False):
        self.responses = list(responses or [])
        self.stream = list(stream or [])
        self.fail = fail
        self.calls = []

    async def acomplete(self, prompt, *, model, json_mode=False):
        self.calls.append(("complete", prompt, model, json_mode))
        if self.fail:
            raise RuntimeError("provider down")
        return self.responses.pop(0), {"prompt_tokens": 100, "completion_tokens": 50}

    async def astream(self, prompt, *, model):
        self.calls.append(("stream", prompt, model))
        if self.fail:
            yield "部", {}
            raise RuntimeError("provider down")
        for item in self.stream:
            yield item


@pytest.fixture
def client():
    provider = FakeProvider()
    sink = FakeSink()
    return LLMClient(provider=provider, sink=sink, provider_name="fake"), provider, sink


def test_acomplete_records_fields_and_cost(client):
    c, provider, sink = client
    provider.responses.append("hello")
    import asyncio
    res = asyncio.run(c.acomplete(
        "hi", agent="test", user_id="u1", session_id="s1", trace_id="t1",
        model="deepseek-chat",
    ))
    assert res.text == "hello"
    rec = sink.records[0]
    assert rec["success"] is True
    assert rec["agent"] == "test"
    assert rec["user_id"] == "u1"
    assert rec["session_id"] == "s1"
    assert rec["trace_id"] == "t1"
    assert rec["model"] == "deepseek-chat"
    assert rec["provider"] == "fake"
    assert rec["prompt_tokens"] == 100
    assert rec["completion_tokens"] == 50
    assert rec["usage_estimated"] is False
    assert rec["streamed"] is False
    assert rec["latency_ms"] >= 0
    # deepseek-chat: (0.27, 1.10) USD/1M
    assert rec["estimated_cost"] == pytest.approx(100 / 1e6 * 0.27 + 50 / 1e6 * 1.10, abs=1e-9)


def test_acomplete_json_mode_parses_data(client):
    c, provider, _ = client
    provider.responses.append(json.dumps({"a": 1}))
    import asyncio
    res = asyncio.run(c.acomplete("prompt with json", json_mode=True))
    assert res.data == {"a": 1}


def test_acomplete_json_mode_invalid_json_gives_none(client):
    c, provider, _ = client
    provider.responses.append("not json")
    import asyncio
    res = asyncio.run(c.acomplete("x", json_mode=True))
    assert res.data is None
    assert res.text == "not json"


def test_acomplete_failure_records_and_raises(client):
    c, provider, sink = client
    provider.fail = True
    import asyncio
    with pytest.raises(RuntimeError):
        asyncio.run(c.acomplete("hi", agent="a"))
    rec = sink.records[0]
    assert rec["success"] is False
    assert "provider down" in rec["error"]
    assert rec["usage_estimated"] is True  # 失败时估算


def test_astream_yields_tokens_and_final_record(client):
    c, provider, sink = client
    provider.stream = [
        ("你", {}), ("好", {}), ("", {"prompt_tokens": 3, "completion_tokens": 2}),
    ]
    import asyncio

    async def run():
        stream = c.astream("hi", agent="dialogue", user_id="u1", session_id="s1", trace_id="t1")
        toks = [t async for t in stream]
        assert toks == ["你", "好"]
        return stream.record

    rec = asyncio.run(run())
    assert rec.success is True
    assert rec.streamed is True
    assert rec.agent == "dialogue"
    assert rec.prompt_tokens == 3
    assert rec.completion_tokens == 2
    assert rec.usage_estimated is False
    assert len(sink.records) == 1


def test_astream_without_usage_estimates(client):
    c, provider, sink = client
    provider.stream = [("你好", {})]
    import asyncio

    async def run():
        stream = c.astream("hi")
        async for _ in stream:
            pass
        return stream.record

    rec = asyncio.run(run())
    assert rec.usage_estimated is True
    assert rec.completion_tokens >= 1
    assert rec.prompt_tokens >= 1


def test_bind_ainvoke_skill_compat(client):
    c, provider, sink = client
    provider.responses.append("skill output")
    import asyncio
    bound = c.bind(agent="therapy_cbt", user_id="u1", session_id="s1", trace_id="t1")
    resp = asyncio.run(bound.ainvoke("prompt"))
    assert resp.content == "skill output"
    assert sink.records[0]["agent"] == "therapy_cbt"


def test_provider_messages_wrapping():
    assert OpenAICompatibleProvider._messages("你好") == [{"role": "user", "content": "你好"}]


class CacheProvider:
    """acomplete 返回带缓存字段的 usage（OpenAI 与 DeepSeek 两种风格分别测）。"""

    def __init__(self, usage):
        self.usage = usage
        self.calls = []

    async def acomplete(self, prompt, *, model, json_mode=False):
        self.calls.append(prompt)
        return "ok", self.usage

    async def astream(self, prompt, *, model):
        import asyncio
        await asyncio.sleep(0.01)  # 模拟真实首 token 延迟（ttft 需要 >0）
        yield "x", {}
        yield "", self.usage


def test_cache_fields_recorded_from_usage():
    import asyncio
    from src.core.llm_client import LLMClient

    class Sink:
        def __init__(self): self.records = []
        def record(self, rec): self.records.append(rec)

    sink = Sink()
    c = LLMClient(provider=CacheProvider({"prompt_tokens": 100, "completion_tokens": 10,
                                          "cache_hit_tokens": 60, "cache_miss_tokens": 40}),
                  sink=sink, provider_name="fake")
    _res = asyncio.run(c.acomplete("p", agent="a"))
    rec = sink.records[0]
    assert rec["cache_hit_tokens"] == 60
    assert rec["cache_miss_tokens"] == 40
    assert rec["ttft_ms"] == 0  # 非流式无 ttft


def test_astream_records_ttft():
    import asyncio

    class Sink:
        def __init__(self): self.records = []
        def record(self, rec): self.records.append(rec)

    sink = Sink()
    c = LLMClient(provider=CacheProvider({"prompt_tokens": 5, "completion_tokens": 1}),
                  sink=sink, provider_name="fake")

    async def run():
        stream = c.astream("p", agent="a")
        async for _ in stream:
            pass
        return stream.record

    rec = asyncio.run(run())
    assert rec.ttft_ms > 0


class _UsageObj:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_usage_dict_openai_style():
    details = _UsageObj(cached_tokens=70)
    usage = _UsageObj(prompt_tokens=100, completion_tokens=20, prompt_tokens_details=details)
    out = __import__("src.core.llm_client", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider._usage_dict(usage)
    assert out["cache_hit_tokens"] == 70


def test_usage_dict_deepseek_style():
    usage = _UsageObj(prompt_tokens=100, completion_tokens=20,
                      prompt_cache_hit_tokens=55, prompt_cache_miss_tokens=45)
    out = __import__("src.core.llm_client", fromlist=["OpenAICompatibleProvider"]).OpenAICompatibleProvider._usage_dict(usage)
    assert out["cache_hit_tokens"] == 55
    assert out["cache_miss_tokens"] == 45
