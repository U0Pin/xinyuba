"""Phase 2 测试：core/scheduler.py 时序与三线行为（全假件，不触网）。"""

import asyncio
import json

import pytest

from src.agents.host_agent import HostDialogueAgent
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import SettlementLine
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
SAFETY_CRISIS = json.dumps({"risk_level": "CRISIS", "risk_type": "suicidal_ideation"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})


@pytest.fixture
def env(tmp_path, monkeypatch):
    """固定模型名与阈值，隔离真实 .env 影响。"""
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10**9)  # 默认不触发摘要 LLM
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)  # 日常态默认不触发疗法决策（避免脚本错位）
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    host = HostDialogueAgent(client, session_store=sessions)
    safety = SafetyAgent(client)
    scheduler = Scheduler(
        dialogue_line=DialogueLine(host, session_store=sessions),
        decision_line=DecisionLine(safety, session_store=sessions),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )
    return {
        "tmp": tmp_path, "client": client, "provider": provider, "sink": sink,
        "sessions": sessions, "profiles": profiles, "scheduler": scheduler,
    }


async def run_turn(scheduler, uid, sid, msg):
    events = []
    async for event in scheduler.handle_message(uid, sid, msg):
        events.append(event)
    return events


def script_turn(env, stream_tokens=("你", "好"), safety=SAFETY_SAFE, profile=PROFILE_NOCHANGE):
    env["provider"].script_stream("test-model", [list(stream_tokens)])
    env["provider"].script_complete("test-cheap", [safety])
    env["provider"].script_complete("test-model", [profile])


class TestBasicTurn:
    def test_turn_flow_and_events(self, env):
        env["provider"].script_stream("test-model", [["你", "好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        sid = env["sessions"].create("u1")
        events = asyncio.run(run_turn(env["scheduler"], "u1", sid, "你好"))
        assert [e["event"] for e in events] == ["token", "token", "dialogue_done", "final"]
        assert events[0]["data"] == "你"
        final = events[-1]["data"]
        assert final["risk_state"] == "SAFE"
        assert final["crisis"] is False
        assert "planned_skill" not in final  # DEBUG_TALKS 未开启

    def test_flow_entries_written_by_dialogue(self, env):
        script_turn(env)
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "你好"))
        flow = env["sessions"].read_flow_all(sid)
        assert [e["role"] for e in flow] == ["user", "agent"]
        assert flow[0]["text"] == "你好"
        assert flow[0]["owner"] == "daily"
        assert flow[1]["text"] == "你好"  # stream 拼出的 "你"+"好"
        assert flow[1]["owner"] == "daily"
        assert flow[1]["tokens"]["completion"] >= 2
        assert flow[1]["trace_id"] == flow[0]["trace_id"]

    def test_state_risk_recorded(self, env):
        script_turn(env, safety=json.dumps({"risk_level": "MEDIUM_RISK", "risk_type": "none"}))
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我今天很焦虑"))
        state = env["sessions"].read_state(sid)
        assert state["risk_level"] == "MEDIUM_RISK"
        assert state["crisis"] is False
        assert state["owner"] == "daily"

    def test_llm_records_have_identity(self, env):
        script_turn(env)
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "hi"))
        agents = {r["agent"] for r in env["sink"].records}
        assert {"daily", "safety", "settlement_profile"} <= agents
        for r in env["sink"].records:
            assert r["user_id"] == "u1"
            assert r["session_id"] == sid
            assert r["trace_id"]


class TestCrisisTiming:
    def test_crisis_effective_next_turn(self, env):
        """危险消息当轮普通回复；下一轮起危机应对；安全恢复后解除。"""
        sid = env["sessions"].create("u1")
        scheduler = env["scheduler"]
        provider = env["provider"]

        # turn1: 危险消息 → 安全判 CRISIS，但本轮回复仍是普通 prompt
        provider.script_stream("test-model", [["我", "在"]])
        provider.script_complete("test-cheap", [SAFETY_CRISIS])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        events1 = asyncio.run(run_turn(scheduler, "u1", sid, "我想结束这一切"))
        assert events1[-1]["data"]["crisis"] is True

        # turn2: 危机应对 prompt（含底稿关键词）
        provider.script_stream("test-model", [["先", "停一停"]])
        provider.script_complete("test-cheap", [SAFETY_CRISIS])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        events2 = asyncio.run(run_turn(scheduler, "u1", sid, "我撑不住了"))
        assert events2[-1]["data"]["crisis"] is True
        stream_calls = [c for c in provider.calls if c[0] == "stream"]
        assert "我在这里陪着你" in stream_calls[1][1]
        assert "我在这里陪着你" not in stream_calls[0][1]

        # turn3: 安全恢复 → 危机解除（回复仍为危机模式，边界后解除）
        provider.script_stream("test-model", [["没", "事"]])
        provider.script_complete("test-cheap", [SAFETY_SAFE])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        events3 = asyncio.run(run_turn(scheduler, "u1", sid, "我缓过来了"))
        assert events3[-1]["data"]["crisis"] is False

        # turn4: 恢复普通回复
        provider.script_stream("test-model", [["好"]])
        provider.script_complete("test-cheap", [SAFETY_SAFE])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        asyncio.run(run_turn(scheduler, "u1", sid, "谢谢你"))
        stream_calls = [c for c in provider.calls if c[0] == "stream"]
        assert "我在这里陪着你" not in stream_calls[-1][1]

    def test_safety_failure_falls_back_safe(self, env):
        env["provider"].script_stream("test-model", [["ok"]])
        env["provider"].script_complete("test-cheap", [RuntimeError("cheap model down")])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        sid = env["sessions"].create("u1")
        events = asyncio.run(run_turn(env["scheduler"], "u1", sid, "hi"))
        assert events[-1]["data"]["risk_state"] == "SAFE"


class TestSettlement:
    def test_profile_incremental_update(self, env):
        env["provider"].script_stream("test-model", [["a"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({
                "updates": {"attachment_style": "anxious"},
                "append_lists": {"core_fears": ["被拒绝"]},
                "no_change": False,
            }),
        ])
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我害怕被同事讨厌"))
        profile = env["profiles"].get("u1")
        assert profile["attachment_style"] == "anxious"
        assert profile["core_fears"] == ["被拒绝"]

    def test_profile_append_dedupes(self, env):
        env["profiles"].save("u1", {"core_fears": ["被拒绝"]})
        env["provider"].script_stream("test-model", [["a"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"updates": {}, "append_lists": {"core_fears": ["被拒绝", "失败"]}, "no_change": False}),
        ])
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我还是怕"))
        assert env["profiles"].get("u1")["core_fears"] == ["被拒绝", "失败"]

    def test_summary_regenerated_on_threshold(self, env, monkeypatch):
        monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 1)  # 触发摘要 LLM
        env["provider"].script_stream("test-model", [["a"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            PROFILE_NOCHANGE,
            "{}",  # portrait_eval（每轮顺带评估，本例不关心其结果）
            json.dumps({"summary": [{"theme": "工作压力", "fact": "失眠", "emotion": "焦虑",
                                     "seq_range": [1, 2]}]}),
        ])
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "最近工作压力很大总失眠"))
        summ = env["sessions"].read_summary(sid)
        assert summ["summary"] == [{"theme": "工作压力", "fact": "失眠", "emotion": "焦虑",
                                    "seq_range": [1, 2]}]
        assert summ["token_estimate"] == 0
        assert summ["updated_seq"] == 2

    def test_summary_skipped_below_threshold(self, env):
        script_turn(env)
        sid = env["sessions"].create("u1")
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "你好"))
        summ = env["sessions"].read_summary(sid)
        assert summ["summary"] == []
        assert summ["updated_seq"] == 2
        # 没有 settlement_summary 调用
        agents = {r["agent"] for r in env["sink"].records}
        assert "settlement_summary" not in agents


class TestConcurrency:
    def test_same_session_serialized(self, env):
        """并发两条消息：session 锁保证流水 seq 严格有序、不交错。"""
        env["provider"].script_stream("test-model", [["a"], ["b"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE, SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE, PROFILE_NOCHANGE])
        sid = env["sessions"].create("u1")

        async def run():
            t1 = run_turn(env["scheduler"], "u1", sid, "m1")
            t2 = run_turn(env["scheduler"], "u1", sid, "m2")
            r = await asyncio.gather(t1, t2)
            return r

        results = asyncio.run(run())
        finals = [r[-1]["data"] for r in results]
        assert {f["risk_state"] for f in finals} == {"SAFE"}
        flow = env["sessions"].read_flow_all(sid)
        seqs = [e["seq"] for e in flow]
        assert seqs == [1, 2, 3, 4]
        assert [e["role"] for e in flow] == ["user", "agent", "user", "agent"]


class TestClientDisconnect:
    """客户端在 SSE 流式中途断开（页面刷新）时，agent 已发出的 token 也必须落库，
    否则用户回到 session 看到的就是「我说了 X → 无回复」。"""

    def test_partial_agent_reply_persisted_on_aclose(self, env):
        provider = env["provider"]
        sessions = env["sessions"]
        # 多给一些 token，确保消费若干个后还能再 yield 出来
        provider.script_stream("test-model", [["你", "好", "呀", "世", "界"]])
        provider.script_complete("test-cheap", [SAFETY_SAFE])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])

        sid = sessions.create("u1")

        async def consume_two_then_close():
            gen = env["scheduler"].handle_message("u1", sid, "你好")
            tokens = []
            try:
                tokens.append(await gen.__anext__())  # token 1
                tokens.append(await gen.__anext__())  # token 2
            finally:
                await gen.aclose()
            return tokens

        tokens = asyncio.run(consume_two_then_close())
        assert [t["event"] for t in tokens] == ["token", "token"]
        assert [t["data"] for t in tokens] == ["你", "好"]

        flow = sessions.read_flow_all(sid)
        assert [e["role"] for e in flow] == ["user", "agent"]
        # 部分 token 被持久化（具体是前两个），关键是有 agent 条目
        assert flow[1]["text"] == "你好"
        assert flow[1]["trace_id"] == flow[0]["trace_id"]
        # 断开时 record 还没被流设置好，不应写出 usage 字段
        assert "tokens" not in flow[1]
