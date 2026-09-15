"""WebUI 测试：页面与 /ui/api/* 接口（假件调度器 + tmp 日志目录）。"""

import json

import pytest
from fastapi.testclient import TestClient

from server import build_app
from src.agents.host_agent import HostDialogueAgent
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import SettlementLine
from src.agents.therapy import (
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.agents.therapy_decider import TherapyDecider
from src.core.llm_client import LLMClient
from src.core.logging_utils import LLMCallSink
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import FakeProvider

import src.skills  # noqa: F401

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10**9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)
    monkeypatch.setattr(config, "LOG_DIR", str(tmp_path))
    provider = FakeProvider()
    client = LLMClient(provider=provider, sink=LLMCallSink(str(tmp_path / "llm_calls.jsonl")))
    sessions = SessionStore(data_root=str(tmp_path / "data"))
    profiles = ProfileStore(data_root=str(tmp_path / "data"))
    therapy_agents = {
        "CBT": CbtTherapyAgent(client, session_store=sessions),
        "ACT": ActTherapyAgent(client, session_store=sessions),
        "DBT": DbtTherapyAgent(client, session_store=sessions),
        "MI": MiTherapyAgent(client, session_store=sessions),
        "SFBT": SfbtTherapyAgent(client, session_store=sessions),
    }
    scheduler = Scheduler(
        dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions),
                                   session_store=sessions),
        decision_line=DecisionLine(
            SafetyAgent(client), session_store=sessions,
            therapy_decider=TherapyDecider(client),
            therapy_agents=therapy_agents,
        ),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )
    app = build_app(scheduler=scheduler)
    with TestClient(app) as tc:
        yield {"client": tc, "provider": provider, "sessions": sessions, "profiles": profiles}


def _script_turn(env):
    env["provider"].script_stream("test-model", [["你", "好"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])


def _sse_final(env, uid, sid, msg):
    with env["client"].stream("POST", "/chat",
                              json={"user_id": uid, "session_id": sid, "message": msg}) as resp:
        final = None
        for line in resp.iter_lines():
            if line.startswith("data: "):
                final = json.loads(line[len("data: "):])
        return final


class TestPage:
    def test_index_served(self, env):
        r = env["client"].get("/ui")
        assert r.status_code == 200
        assert "<html" in r.text.lower()
        assert "EA Test Console" in r.text


class TestOverview:
    def test_sessions_listed_with_state(self, env):
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        r = env["client"].get("/ui/api/overview", params={"user_id": "u1"})
        sessions = r.json()["sessions"]
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["owner"] == "daily"
        assert sessions[0]["therapy"] is None
        assert sessions[0]["flow_lines"] == 0


class TestUsers:
    def test_users_listed_from_disk(self, env):
        env["client"].post("/sessions", json={"user_id": "alice"})
        env["client"].post("/sessions", json={"user_id": "bob"})
        r = env["client"].get("/ui/api/users")
        assert r.json()["users"] == ["alice", "bob"]

    def test_create_user_endpoint(self, env):
        r = env["client"].post("/ui/api/user", json={"user_id": "carol"})
        assert r.status_code == 200
        users = env["client"].get("/ui/api/users").json()["users"]
        assert "carol" in users
        # 空 user：无 session
        overview = env["client"].get("/ui/api/overview", params={"user_id": "carol"}).json()
        assert overview["sessions"] == []

    def test_create_user_rejects_blank(self, env):
        r = env["client"].post("/ui/api/user", json={"user_id": "  "})
        assert r.status_code == 400


class TestState:
    def test_state_endpoint_returns_artifacts(self, env):
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        _script_turn(env)
        _sse_final(env, "u1", sid, "你好")
        data = env["client"].get(f"/ui/api/session/{sid}/state").json()
        assert data["state"]["owner"] == "daily"
        assert data["state"]["risk_level"] == "SAFE"
        assert len(data["flow_tail"]) == 2
        assert data["summary"]["updated_seq"] == 2

    def test_state_404(self, env):
        assert env["client"].get("/ui/api/session/ghost/state").status_code == 404

    def test_profile_endpoint(self, env):
        r = env["client"].get("/ui/api/user/u1/profile")
        assert r.json() == {"user_id": "u1", "profile": {}}


class TestMetrics:
    def test_metrics_aggregate_from_llm_calls(self, env):
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        _script_turn(env)
        _sse_final(env, "u1", sid, "你好")
        m = env["client"].get(f"/ui/api/session/{sid}/metrics").json()
        assert m["total_tokens"] > 0
        assert m["calls"] >= 3  # daily(流式) + safety + settlement_profile
        assert m["avg_ttft_ms"] is not None  # 流式调用有 ttft
        assert m["avg_latency_ms"] is not None
        assert m["estimated_cost_usd"] >= 0
        assert "daily" in m["by_agent"]
        assert "safety" in m["by_agent"]
        assert m["cache_hit_rate"] is None  # 假 provider 未报告缓存

    def test_metrics_incremental(self, env):
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        _script_turn(env)
        _sse_final(env, "u1", sid, "你好")
        m1 = env["client"].get(f"/ui/api/session/{sid}/metrics").json()
        _script_turn(env)
        _sse_final(env, "u1", sid, "你好呀")
        m2 = env["client"].get(f"/ui/api/session/{sid}/metrics").json()
        assert m2["calls"] > m1["calls"]
        assert m2["total_tokens"] > m1["total_tokens"]


class TestLogs:
    def test_logs_endpoint_has_both_sources(self, env):
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        _script_turn(env)
        _sse_final(env, "u1", sid, "你好")
        data = env["client"].get("/ui/api/logs").json()
        assert len(data["app"]) >= 2  # turn_start / turn_end 等
        assert len(data["llm"]) >= 3
        assert data["app"][0]["line"] >= 1
        llm_data = [e["data"] for e in data["llm"]]
        assert any(d["session_id"] == sid for d in llm_data)
