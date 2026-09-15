"""结构化日志与 trace 关联（新架构，Phase 1）。

- 一次用户消息一个 trace_id，贯穿安全/编排/对话/沉淀的全部 LLM 调用与模块日志；
- 日志尽量详细、分级处理；**包含对话原文**（作者已确认）；
- 落盘：`logs/app.jsonl`（模块工作记录）+ `logs/llm_calls.jsonl`（LLM 记账，由 LLMCallSink 写）。
"""

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

from src.utils.config import config

_write_lock = threading.Lock()

# 日志统一使用北京时间（UTC+8，中国无夏令时）
_BEIJING_TZ = timezone(timedelta(hours=8))


def new_trace_id() -> str:
    """为一次用户消息生成 trace_id。"""
    return uuid.uuid4().hex[:16]


def now_iso() -> str:
    """当前北京时间 ISO 字符串（全系统存盘/日志时间戳统一走这里）。"""
    return datetime.now(_BEIJING_TZ).isoformat()


def _append_line(path: str, obj: dict) -> None:
    """追加一行 JSON（同步写；行很短、频率远低于 LLM 延迟，无需异步）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with _write_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def app_log(level: str, module: str, event: str, trace_id: str = "", **fields) -> None:
    """写一条结构化分级日志（debug/info/warning/error）。

    调用方通过 **fields 附带任意详情（含对话原文等）。所有模块的工作记录
    都必须走这里，保证"每个部分的工作记录都有迹可循"。
    """
    entry = {
        "ts": now_iso(),
        "level": level,
        "module": module,
        "event": event,
        "trace_id": trace_id,
        **fields,
    }
    _append_line(os.path.join(config.LOG_DIR, "app.jsonl"), entry)


class LLMCallSink:
    """统一 LLM 接口的记账落盘器：`logs/llm_calls.jsonl` 每调用一行。

    LLMClient 在每次调用（成功或失败）结束后调用 record()。
    """

    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(config.LOG_DIR, "llm_calls.jsonl")

    def record(self, rec: dict) -> None:
        _append_line(self.path, rec)
