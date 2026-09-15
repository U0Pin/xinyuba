"""统一 LLM 调用接口（新架构，Phase 1）。

- provider 抽象（可换模型/换服务商）；
- 记账字段齐全：user / session / agent / 时间 / token / model / provider /
  latency / success·failure / request ID / estimated cost（目前只记账不限制）；
- 两个方法：`astream`（对话线用，流式）+ `acomplete`（决策线/沉淀线用，非流式）；
- `bind()` 产出与旧 skill 层兼容的客户端（含 `.ainvoke()` 返回带 `.content` 的对象，
  使 `src.core.skill.llm_json_async` 无需改动即可使用本接口）。

所有 LLM 调用（含 skill 执行）都必须经由本类，保证记账完整。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import AsyncIterator, Optional, Protocol

from src.core.logging_utils import LLMCallSink, app_log, now_iso
from src.utils.config import config
from src.utils.text import est_tokens_quarter


@dataclass
class LLMCallRecord:
    """一次 LLM 调用的完整记账。"""

    request_id: str = ""
    user_id: str = ""
    session_id: str = ""
    agent: str = ""
    ts: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    ttft_ms: float = 0.0             # 首 token 时间（仅流式调用；非流式为 0）
    success: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0        # 命中的缓存 token（provider 报告时才有值）
    cache_miss_tokens: int = 0       # 未命中的缓存 token（provider 报告时才有值）
    usage_estimated: bool = False
    estimated_cost: float = 0.0
    streamed: bool = False
    trace_id: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LLMResult:
    """非流式调用的结果。"""

    text: str
    record: LLMCallRecord
    data: Optional[dict] = None  # json_mode=True 时解析出的 JSON（失败为 None）


class LLMProvider(Protocol):
    """provider 抽象：任何可换的 LLM 服务商实现这两个方法。"""

    async def acomplete(self, prompt: str, *, model: str, json_mode: bool = False) -> tuple[str, dict]:
        """返回 (text, usage)；usage 为 {} 或缺字段时由 LLMClient 估算。"""
        ...

    def astream(self, prompt: str, *, model: str) -> AsyncIterator[tuple[str, dict]]:
        """产出 (文本片段, usage)；usage 通常只在末尾片段携带。"""
        ...


class OpenAICompatibleProvider:
    """OpenAI 兼容 provider（openai SDK，支持 DeepSeek/Qwen/OpenAI 等）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        import openai

        self._client = openai.AsyncOpenAI(
            api_key=api_key or config.OPENAI_API_KEY,
            base_url=base_url or config.OPENAI_BASE_URL,
        )
        self.temperature = temperature if temperature is not None else config.TEMPERATURE
        self.max_tokens = max_tokens or config.MAX_TOKENS

    @staticmethod
    def _messages(prompt: str) -> list[dict]:
        return [{"role": "user", "content": prompt}]

    @staticmethod
    def _usage_dict(usage) -> dict:
        """把 openai SDK 的 usage 对象压成 dict（含缓存命中字段）。

        兼容两种缓存报告风格：
        - OpenAI 风格：usage.prompt_tokens_details.cached_tokens
        - DeepSeek 风格：usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
        """
        if not usage:
            return {}
        out = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None and getattr(details, "cached_tokens", None) is not None:
            out["cache_hit_tokens"] = details.cached_tokens or 0
        hit = getattr(usage, "prompt_cache_hit_tokens", None)
        miss = getattr(usage, "prompt_cache_miss_tokens", None)
        if hit is not None or miss is not None:
            out["cache_hit_tokens"] = hit or 0
            out["cache_miss_tokens"] = miss or 0
        return out

    async def acomplete(self, prompt: str, *, model: str, json_mode: bool = False) -> tuple[str, dict]:
        kwargs: dict = dict(
            model=model,
            messages=self._messages(prompt),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except Exception:
            if json_mode:
                # 个别 provider 不支持 response_format → 去掉重试一次
                kwargs.pop("response_format")
                resp = await self._client.chat.completions.create(**kwargs)
            else:
                raise
        text = (resp.choices[0].message.content if resp.choices else "") or ""
        return text, self._usage_dict(getattr(resp, "usage", None))

    async def astream(self, prompt: str, *, model: str) -> AsyncIterator[tuple[str, dict]]:
        kwargs: dict = dict(
            model=model,
            messages=self._messages(prompt),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        try:
            stream = await self._client.chat.completions.create(
                **kwargs, stream_options={"include_usage": True}
            )
        except TypeError:
            # 旧版 SDK 不支持 stream_options → 不带 usage 重试
            stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                yield "", self._usage_dict(getattr(chunk, "usage", None))
                continue
            delta = chunk.choices[0].delta
            yield (delta.content or ""), {}


class LLMStream:
    """流式调用的句柄：迭代产出 token 文本；流结束后可读 `.record`。"""

    def __init__(self, agen: AsyncIterator[str], holder: dict):
        self._agen = agen
        self._holder = holder
        self.record: Optional[LLMCallRecord] = None

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await self._agen.__anext__()
        except StopAsyncIteration:
            self.record = self._holder.get("record")
            raise


class SimpleResp:
    """兼容旧 skill 层的响应对象（`llm_json_async` 只读 `.content`）。"""

    def __init__(self, content: str):
        self.content = content


class LLMClient:
    """统一 LLM 接口。provider 默认 OpenAI 兼容实现。"""

    def __init__(
        self,
        provider: LLMProvider | None = None,
        sink: LLMCallSink | None = None,
        provider_name: str = "openai-compatible",
    ):
        # 默认 provider 惰性实例化：组装（含 import）不要求凭据，
        # 只有真正发起调用时才构建 OpenAI 兼容客户端。
        self._provider = provider
        self._sink = sink or LLMCallSink()
        self.provider_name = provider_name

    @property
    def provider(self) -> LLMProvider:
        if self._provider is None:
            self._provider = OpenAICompatibleProvider()
        return self._provider

    def bind(
        self,
        *,
        agent: str,
        user_id: str = "",
        session_id: str = "",
        trace_id: str = "",
    ) -> "BoundLLMClient":
        """绑定调用者身份的客户端（skill 执行与各 Agent 统一走这里）。"""
        return BoundLLMClient(
            self, agent=agent, user_id=user_id, session_id=session_id, trace_id=trace_id
        )

    # ── 核心实现 ────────────────────────────────────────────────────

    def _new_record(
        self,
        *,
        agent: str,
        user_id: str,
        session_id: str,
        trace_id: str,
        model: str,
        streamed: bool,
    ) -> LLMCallRecord:
        return LLMCallRecord(
            request_id=uuid.uuid4().hex[:16],
            user_id=user_id,
            session_id=session_id,
            agent=agent,
            ts=now_iso(),
            model=model,
            provider=self.provider_name,
            streamed=streamed,
            trace_id=trace_id,
        )

    @staticmethod
    def _finalize(
        rec: LLMCallRecord,
        *,
        prompt: str,
        text: str,
        usage: dict,
        latency_ms: float,
        success: bool,
        error: str = "",
    ) -> None:
        rec.latency_ms = round(latency_ms, 1)
        rec.success = success
        rec.error = error
        rec.completion_tokens = int(usage.get("completion_tokens", 0))
        rec.prompt_tokens = int(usage.get("prompt_tokens", 0))
        rec.cache_hit_tokens = int(usage.get("cache_hit_tokens", 0))
        rec.cache_miss_tokens = int(usage.get("cache_miss_tokens", 0))
        if "completion_tokens" not in usage and "prompt_tokens" not in usage:
            rec.usage_estimated = True
            rec.completion_tokens = est_tokens_quarter(text)
            rec.prompt_tokens = est_tokens_quarter(prompt)
        price_in, price_out = config.model_price(rec.model)
        rec.estimated_cost = round(
            rec.prompt_tokens / 1_000_000 * price_in
            + rec.completion_tokens / 1_000_000 * price_out,
            8,
        )

    async def acomplete(
        self,
        prompt: str,
        *,
        agent: str = "",
        user_id: str = "",
        session_id: str = "",
        trace_id: str = "",
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResult:
        model = model or config.MODEL_NAME
        rec = self._new_record(
            agent=agent, user_id=user_id, session_id=session_id,
            trace_id=trace_id, model=model, streamed=False,
        )
        t0 = time.perf_counter()
        try:
            text, usage = await self.provider.acomplete(prompt, model=model, json_mode=json_mode)
            self._finalize(rec, prompt=prompt, text=text, usage=usage,
                           latency_ms=(time.perf_counter() - t0) * 1000, success=True)
        except Exception as e:  # noqa: BLE001 — 记账后原样抛出，由调用方兜底
            self._finalize(rec, prompt=prompt, text="", usage={},
                           latency_ms=(time.perf_counter() - t0) * 1000, success=False, error=str(e))
            self._sink.record(rec.to_dict())
            app_log("error", "llm_client", "complete_failed", trace_id=trace_id,
                    agent=agent, model=model, error=str(e))
            raise
        self._sink.record(rec.to_dict())
        data = None
        if json_mode:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = None
        return LLMResult(text=text, record=rec, data=data)

    def astream(
        self,
        prompt: str,
        *,
        agent: str = "",
        user_id: str = "",
        session_id: str = "",
        trace_id: str = "",
        model: str | None = None,
    ) -> LLMStream:
        model = model or config.MODEL_NAME
        rec = self._new_record(
            agent=agent, user_id=user_id, session_id=session_id,
            trace_id=trace_id, model=model, streamed=True,
        )
        holder: dict = {}
        t0 = time.perf_counter()

        async def gen() -> AsyncIterator[str]:
            parts: list[str] = []
            usage: dict = {}
            first_token = True
            try:
                async for chunk_text, chunk_usage in self.provider.astream(prompt, model=model):
                    if chunk_usage:
                        usage = chunk_usage
                    if chunk_text:
                        if first_token:
                            rec.ttft_ms = round((time.perf_counter() - t0) * 1000, 1)
                            first_token = False
                        parts.append(chunk_text)
                        yield chunk_text
                text = "".join(parts)
                self._finalize(rec, prompt=prompt, text=text, usage=usage,
                               latency_ms=(time.perf_counter() - t0) * 1000, success=True)
            except Exception as e:  # noqa: BLE001 — 记账后继续抛出，让调用方感知中断
                text = "".join(parts)
                self._finalize(rec, prompt=prompt, text=text, usage=usage,
                               latency_ms=(time.perf_counter() - t0) * 1000, success=False, error=str(e))
                self._sink.record(rec.to_dict())
                app_log("error", "llm_client", "stream_failed", trace_id=trace_id,
                        agent=agent, model=model, error=str(e))
                raise
            self._sink.record(rec.to_dict())
            holder["record"] = rec

        return LLMStream(gen(), holder)


class BoundLLMClient:
    """绑定调用者身份的客户端；`.ainvoke()` 兼容旧 skill 层。"""

    def __init__(self, client: LLMClient, *, agent: str, user_id: str, session_id: str, trace_id: str):
        self._client = client
        self.agent = agent
        self.user_id = user_id
        self.session_id = session_id
        self.trace_id = trace_id

    async def ainvoke(self, prompt: str) -> SimpleResp:
        """兼容 `llm_json_async` / skill 的 `await llm.ainvoke(prompt)` 调用。"""
        result = await self._client.acomplete(
            prompt,
            agent=self.agent,
            user_id=self.user_id,
            session_id=self.session_id,
            trace_id=self.trace_id,
        )
        return SimpleResp(result.text)

    async def acomplete(self, prompt: str, *, model: str | None = None, json_mode: bool = False) -> LLMResult:
        return await self._client.acomplete(
            prompt,
            agent=self.agent,
            user_id=self.user_id,
            session_id=self.session_id,
            trace_id=self.trace_id,
            model=model,
            json_mode=json_mode,
        )

    async def ajson(
        self,
        prompt: str,
        *,
        model: str | None = None,
        failure_log: tuple[str, str] | None = None,
    ) -> dict:
        """请求一个 JSON 对象；调用异常或解析失败都返回 {}（决策侧兜底）。

        failure_log=(module, event) 提供时在异常路径写一条 warning 日志
        （解析失败不落日志，与此前各调用点的行为一致）。
        """
        try:
            result = await self.acomplete(prompt, model=model, json_mode=True)
        except Exception as e:  # noqa: BLE001 — 兜底返回 {}，由调用方决定降级语义
            if failure_log is not None:
                app_log("warning", failure_log[0], failure_log[1],
                        trace_id=self.trace_id, error=str(e))
            return {}
        return result.data or {}

    def astream(self, prompt: str, *, model: str | None = None) -> LLMStream:
        return self._client.astream(
            prompt,
            agent=self.agent,
            user_id=self.user_id,
            session_id=self.session_id,
            trace_id=self.trace_id,
            model=model,
        )
