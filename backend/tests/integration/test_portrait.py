"""画像页 / 设置页数据需求测试：ACT 六维 + 人格 + 价值词 + 用户信息同步。"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from server import build_app
from src.agents.host_agent import HostDialogueAgent
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import (
    SettlementLine,
    parse_act_metrics,
    parse_personality,
)
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.portrait_store import PortraitStore
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.store.user_info_store import UserInfoStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
PORTRAIT_RESULT = json.dumps({
    "act_metrics": {
        "psychological_flexibility": 0.6,
        "emotional_openness": 0.7,
        "cognitive_readiness": 0.5,
        "cognitive_fusion": 0.4,
        "experiential_avoidance": 0.3,
        "values_alignment": 0.8,
    },
    "personality": "navigator",
    "value_words": ["家庭", "自由"],
})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10**9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    portraits = PortraitStore(data_root=str(tmp_path))
    user_infos = UserInfoStore(data_root=str(tmp_path))
    scheduler = Scheduler(
        dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions), session_store=sessions),
        decision_line=DecisionLine(SafetyAgent(client), session_store=sessions),
        settlement_line=SettlementLine(
            client, session_store=sessions, profile_store=profiles, portrait_store=portraits,
        ),
        session_store=sessions,
        profile_store=profiles,
        user_info_store=user_infos,
    )
    return {
        "provider": provider, "sessions": sessions, "profiles": profiles,
        "portraits": portraits, "user_infos": user_infos, "scheduler": scheduler,
        "client": TestClient(build_app(scheduler=scheduler)),
    }


# ── 解析校验 ────────────────────────────────────────────────────────


def test_parse_act_metrics_filters_invalid():
    data = {"act_metrics": {
        "psychological_flexibility": 0.6,
        "emotional_openness": 1.5,       # 越界丢弃
        "cognitive_readiness": "high",   # 非数值丢弃
        "cognitive_fusion": 0,           # 边界合法
        "unknown_dim": 0.5,              # 未知维度丢弃
    }}
    assert parse_act_metrics(data) == {
        "psychological_flexibility": 0.6,
        "cognitive_fusion": 0.0,
    }


def test_parse_act_metrics_non_dict():
    assert parse_act_metrics({"act_metrics": "bad"}) == {}
    assert parse_act_metrics({}) == {}


def test_parse_personality_enum():
    assert parse_personality({"personality": "anchor"}) == "anchor"
    assert parse_personality({"personality": "superman"}) is None
    assert parse_personality({}) is None


# ── 沉淀线：每轮顺带评估 + 人格重判 ──────────────────────────────────


def test_settlement_evaluates_portrait_after_turn(env):
    env["profiles"].save("u1", {"attachment_style": "anxious"})
    env["provider"].script_complete("test-model", [PORTRAIT_RESULT])
    ok = asyncio.run(env["scheduler"].settlement_line.regenerate_personality("u1"))
    assert ok is True
    assert env["portraits"].get("u1")["personality"] == "navigator"


def test_regenerate_only_touches_personality(env):
    env["portraits"].save("u1", {
        "act_metrics": {"values_alignment": 0.8},
        "personality": "beginner",
        "value_words": ["家庭"],
    })
    env["profiles"].save("u1", {"attachment_style": "anxious"})
    env["provider"].script_complete("test-model", [PORTRAIT_RESULT])
    asyncio.run(env["scheduler"].settlement_line.regenerate_personality("u1"))
    portrait = env["portraits"].get("u1")
    assert portrait["personality"] == "navigator"
    assert portrait["act_metrics"] == {"values_alignment": 0.8}  # 六维不动
    assert portrait["value_words"] == ["家庭"]                   # 价值词不动


def test_regenerate_without_profile_still_works(env):
    """无需画像积累：画像为空时也基于保守判定给出人格。"""
    env["provider"].script_complete("test-model", [PORTRAIT_RESULT])
    ok = asyncio.run(env["scheduler"].settlement_line.regenerate_personality("u1"))
    assert ok is True
    assert env["portraits"].get("u1")["personality"] == "navigator"


def test_first_turn_without_profile_produces_portrait(env):
    """首轮对话（画像仍为空）即可产出六维 + 人格 + 价值词，不再一直 pending。"""
    env["provider"].script_complete("test-model", [
        json.dumps({"no_change": True}),  # 画像增量无产出 → profile 仍为空
        PORTRAIT_RESULT,
    ])
    sid = env["sessions"].create("u1")
    env["sessions"].append_flow(sid, {"role": "user", "text": "我最近很焦虑"})
    env["sessions"].append_flow(sid, {"role": "agent", "text": "我在听"})
    asyncio.run(env["scheduler"].settlement_line.run_after_turn(
        user_id="u1", session_id=sid, trace_id="t1"))
    assert env["profiles"].get("u1") == {}  # 画像确实为空
    portrait = env["portraits"].get("u1")
    assert portrait["act_metrics"]["values_alignment"] == 0.8
    assert portrait["personality"] == "navigator"
    assert portrait["value_words"] == ["家庭", "自由"]


def test_update_portrait_via_run_after_turn(env):
    """一轮结束沉淀线顺带评估：画像增量 + 画像页评估依次消费脚本。"""
    env["provider"].script_complete("test-model", [
        json.dumps({"updates": {"attachment_style": "anxious"}, "no_change": False}),
        PORTRAIT_RESULT,
    ])
    sid = env["sessions"].create("u1")
    env["sessions"].append_flow(sid, {"role": "user", "text": "我最近很焦虑"})
    env["sessions"].append_flow(sid, {"role": "agent", "text": "我在听"})
    asyncio.run(env["scheduler"].settlement_line.run_after_turn(
        user_id="u1", session_id=sid, trace_id="t1"))
    portrait = env["portraits"].get("u1")
    assert portrait["act_metrics"]["values_alignment"] == 0.8
    assert portrait["personality"] == "navigator"


# ── HTTP 端点 ───────────────────────────────────────────────────────


def test_get_portrait_empty_returns_empty_object(env):
    assert env["client"].get("/users/u1/portrait").json() == {}


def test_get_portrait_ready_modules(env):
    env["portraits"].save("u1", {
        "act_metrics": {"values_alignment": 0.8},
        "personality": "anchor",
        "value_words": ["成长"],
    })
    env["profiles"].save("u1", {"personal_values": ["家庭", "自由"]})
    body = env["client"].get("/users/u1/portrait").json()
    assert body["act_metrics"] == {"status": "ready", "values": {"values_alignment": 0.8}}
    assert body["personality"] == {"status": "ready", "value": "anchor"}
    assert body["value_words"] == {"status": "ready", "values": ["成长"]}  # portrait.json 优先


def test_get_portrait_none_vs_pending(env):
    """三态语义：从未评估 = none；评估进行中 = pending；算完没提取到价值词 = ready + []。"""
    env["profiles"].save("u1", {"personal_values": ["家庭"]})
    body = env["client"].get("/users/u1/portrait").json()
    assert body["act_metrics"] == {"status": "none"}      # 从未算过，不是 pending
    assert body["personality"] == {"status": "none"}
    assert body["value_words"] == {"status": "ready", "values": ["家庭"]}  # 画像兜底

    env["scheduler"].settlement_line.portrait_pending.add("u1")  # 模拟评估进行中
    body = env["client"].get("/users/u1/portrait").json()
    assert body["act_metrics"] == {"status": "pending"}
    assert body["personality"] == {"status": "pending"}


def test_get_portrait_empty_value_words_is_ready(env):
    """价值词算完但没提取到：ready + 空列表（空态），不是 pending。"""
    env["portraits"].save("u1", {"value_words": []})
    body = env["client"].get("/users/u1/portrait").json()
    assert body["value_words"] == {"status": "ready", "values": []}
    assert body["act_metrics"] == {"status": "none"}


def test_value_words_empty_list_persisted(env):
    """LLM 返回空价值词列表也落盘（区分「没有」与「还没算」）。"""
    result = json.loads(PORTRAIT_RESULT)
    result["value_words"] = []
    env["provider"].script_complete("test-model", [json.dumps(result)])
    sid = env["sessions"].create("u1")
    env["sessions"].append_flow(sid, {"role": "user", "text": "随便聊聊"})
    env["sessions"].append_flow(sid, {"role": "agent", "text": "好的"})
    asyncio.run(env["scheduler"].settlement_line._update_portrait("u1", sid, "t1"))
    assert env["portraits"].get("u1")["value_words"] == []


def test_regenerate_endpoint_rate_limited(env):
    env["profiles"].save("u1", {"attachment_style": "anxious"})
    env["provider"].script_complete("test-model", [PORTRAIT_RESULT])
    resp = env["client"].post("/users/u1/portrait/personality/regenerate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    resp2 = env["client"].post("/users/u1/portrait/personality/regenerate")
    assert resp2.status_code == 429


def test_regenerate_endpoint_no_profile_still_accepted(env):
    """无画像积累也可触发重判（保守判定），不再 404。"""
    env["provider"].script_complete("test-model", [PORTRAIT_RESULT])
    resp = env["client"].post("/users/u1/portrait/personality/regenerate")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_put_user_info(env):
    resp = env["client"].put("/users/u1/user-info", json={
        "nickname": "阿黄", "gender": "female", "age": 28, "tone_preference": "gentle",
    })
    assert resp.status_code == 200
    assert resp.json()["user_info"] == {
        "nickname": "阿黄", "gender": "female", "age": 28, "tone_preference": "gentle",
    }
    assert env["user_infos"].get("u1")["nickname"] == "阿黄"


def test_put_user_info_nulls_cleared(env):
    """「重置形象」：PUT 全空值 → 四项清空为未设置。"""
    env["client"].put("/users/u1/user-info", json={"nickname": "阿黄", "age": 28})
    resp = env["client"].put("/users/u1/user-info", json={})
    assert resp.json()["user_info"] == {}
    assert env["user_infos"].get("u1") == {}


@pytest.mark.parametrize("payload", [
    {"nickname": "x" * 13},
    {"gender": "other"},
    {"age": 0},
    {"age": 121},
    {"tone_preference": "angry"},
])
def test_put_user_info_validation(env, payload):
    assert env["client"].put("/users/u1/user-info", json=payload).status_code == 422


def test_user_info_visible_to_agent(env):
    """PUT 后调度器把 user_info 并入画像，随系统提示词对 Agent 可见。"""
    env["client"].put("/users/u1/user-info", json={"nickname": "阿黄"})
    env["provider"].script_stream("test-model", [["你", "好"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [json.dumps({"no_change": True})])
    sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
    with env["client"].stream("POST", "/chat", json={
        "user_id": "u1", "session_id": sid, "message": "你好",
    }) as resp:
        list(resp.iter_lines())
    host_prompt = next(p for kind, p, *_ in env["provider"].calls if kind == "stream")
    assert "阿黄" in host_prompt
