"""Phase 2+ 测试共用假件：脚本化 provider + 内存记账 sink。"""

import asyncio

from src.core.llm_client import LLMClient


class FakeSink:
    def __init__(self):
        self.records = []

    def record(self, rec):
        self.records.append(rec)


class FakeProvider:
    """脚本化 provider。

    - complete：按 model 分桶，`script_complete(model, [resp, ...])`，
      resp 为 str（作为 LLM 返回文本）或 Exception（直接抛出）；
    - stream：按 model 分桶，`script_stream(model, [ [tok, ...], ... ])`，
      每次 astream 消费一个 token 列表。
    """

    def __init__(self):
        self.complete_scripts = {}
        self.stream_scripts = {}
        self.calls = []  # (kind, prompt, model, json_mode)

    def script_complete(self, model, responses):
        bucket = self.complete_scripts.setdefault(model, [])
        bucket.extend(responses)

    def script_stream(self, model, chunks_lists):
        bucket = self.stream_scripts.setdefault(model, [])
        bucket.extend(chunks_lists)

    async def acomplete(self, prompt, *, model, json_mode=False):
        self.calls.append(("complete", prompt, model, json_mode))
        bucket = self.complete_scripts.get(model) or self.complete_scripts.get("*") or []
        if not bucket:
            raise AssertionError(f"FakeProvider: no scripted complete response for model={model}")
        resp = bucket.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp, {"prompt_tokens": 10, "completion_tokens": 5}

    async def astream(self, prompt, *, model):
        self.calls.append(("stream", prompt, model))
        bucket = self.stream_scripts.get(model) or self.stream_scripts.get("*") or []
        if not bucket:
            raise AssertionError(f"FakeProvider: no scripted stream for model={model}")
        chunks = bucket.pop(0)
        if chunks:
            await asyncio.sleep(0.005)  # 模拟真实首 token 延迟（ttft 指标需要 >0）
        for c in chunks:
            yield c, {}
        yield "", {"prompt_tokens": 10, "completion_tokens": max(5, sum(len(c) for c in chunks))}


def make_fake_client():
    provider = FakeProvider()
    sink = FakeSink()
    client = LLMClient(provider=provider, sink=sink, provider_name="fake")
    return client, provider, sink
