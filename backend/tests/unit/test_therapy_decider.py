"""TherapyDecider 错误可见化测试（2026-09 fix）。

之前 `bound.ajson` 不传 `failure_log`，LLM 调用失败/超时/限流被静默吞掉，
看起来"为什么疗法没触发"——实际是日志空白。修复后：
  1. ajson 失败必须写一条 `decision.therapy_decide_failed` warning（真实落盘）；
  2. 模型答 `need_therapy=true` 但 `therapy` 不在 THERAPIES 枚举里（含 missing/幻觉）
     必须写一条 `decision.therapy_decide_invalid` warning；
  3. 正常路径不应写任何 warning。
"""

import asyncio
import json
import os

from src.agents.therapy_decider import TherapyDecider
from src.core.scheduler import TurnContext
from src.utils.config import config
from tests.support.fakes import make_fake_client


def _ctx(user_id="u1", session_id="s1", msg="他肯定对我有意见"):
    return TurnContext(
        user_id=user_id, session_id=session_id, trace_id="t1",
        message=msg,
        state={"owner": "daily", "risk_level": "SAFE", "therapy": None,
               "last_therapy_check_seq": 0, "pending_user_tokens": 0,
               "affect_labeling": None, "pmr": None, "crisis": False},
        profile={}, summary={}, orchestration=None,
    )


def _app_log_events(tmp_path, monkeypatch):
    """把 app_log 指到临时 LOG_DIR，返回"读取已落盘 app.jsonl 的行事件"函数。"""
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    def read():
        p = os.path.join(str(tmp_path), "app.jsonl")
        if not os.path.exists(p):
            return []
        return [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]
    return read


def test_llm_failure_emits_warning_log(tmp_path, monkeypatch):
    """provider 抛异常 → 必须落盘一条 therapy_decide_failed warning。"""
    read_log = _app_log_events(tmp_path, monkeypatch)
    client, provider, _ = make_fake_client()

    async def boom(*a, **kw):
        raise RuntimeError("provider timeout")
    provider.acomplete = boom

    decider = TherapyDecider(client)
    decision = asyncio.run(decider.decide(_ctx(), window=[]))
    assert decision == {"need_therapy": False, "therapy": None, "reason": ""}

    events = read_log()
    hits = [e for e in events if e.get("event") == "therapy_decide_failed" and e.get("level") == "warning"]
    assert hits, f"expected therapy_decide_failed warning on disk; got {events}"
    assert "provider timeout" in hits[-1].get("error", "")


def test_invalid_therapy_name_emits_warning_log(tmp_path, monkeypatch):
    """模型说"需要疗法"但给非枚举疗法名 → 落盘 therapy_decide_invalid warning。"""
    read_log = _app_log_events(tmp_path, monkeypatch)
    client, provider, _ = make_fake_client()
    provider.script_complete("test-model", [
        json.dumps({"need_therapy": True, "therapy": "BPT", "reason": "幻觉的疗法名"})
    ])

    decider = TherapyDecider(client)
    decision = asyncio.run(decider.decide(_ctx(), window=[]))
    assert decision == {"need_therapy": False, "therapy": None, "reason": "幻觉的疗法名"}

    events = read_log()
    hits = [e for e in events if e.get("event") == "therapy_decide_invalid"]
    assert hits, f"expected therapy_decide_invalid on disk; got {events}"
    assert hits[-1].get("raw_therapy") == "BPT"
    assert hits[-1].get("need_therapy") is True


def test_invalid_therapy_name_no_log_when_need_false(tmp_path, monkeypatch):
    """need=false + 非枚举 → 不写 invalid warning（模型已说"不需要"，非异常）。"""
    read_log = _app_log_events(tmp_path, monkeypatch)
    client, provider, _ = make_fake_client()
    provider.script_complete("test-model", [
        json.dumps({"need_therapy": False, "therapy": "BPT", "reason": "闲聊"})
    ])

    decider = TherapyDecider(client)
    decision = asyncio.run(decider.decide(_ctx(), window=[]))
    assert decision == {"need_therapy": False, "therapy": None, "reason": "闲聊"}
    assert not any(e.get("event") == "therapy_decide_invalid" for e in read_log()), \
        f"unexpected therapy_decide_invalid; got {read_log()}"


def test_happy_path_no_warning(tmp_path, monkeypatch):
    """正常路径不写任何 warning。"""
    read_log = _app_log_events(tmp_path, monkeypatch)
    client, provider, _ = make_fake_client()
    provider.script_complete("test-model", [
        json.dumps({"need_therapy": True, "therapy": "CBT", "reason": "明显 CBT 信号"})
    ])

    decider = TherapyDecider(client)
    decision = asyncio.run(decider.decide(_ctx(), window=[]))
    assert decision["need_therapy"] is True
    assert decision["therapy"] == "CBT"
    assert decision["reason"] == "明显 CBT 信号"
    warns = [e for e in read_log() if e.get("level") == "warning"]
    assert not warns, f"expected no warning; got {warns}"
