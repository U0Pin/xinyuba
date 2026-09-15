"""server.py 新 HTTP 接口测试（TestClient + 假件调度器）。"""

import json

import pytest
from fastapi.testclient import TestClient

from server import build_app
from src.agents.host_agent import HostDialogueAgent
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import SettlementLine
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10**9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    scheduler = Scheduler(
        dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions), session_store=sessions),
        decision_line=DecisionLine(SafetyAgent(client), session_store=sessions),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )
    return {
        "provider": provider, "sink": sink, "sessions": sessions,
        "profiles": profiles,
        "client": TestClient(build_app(scheduler=scheduler)),
    }


def _script_turn(env):
    env["provider"].script_stream("test-model", [["你", "好", "呀"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])


def _sse_events(resp):
    events = []
    event_name = None
    for line in resp.iter_lines():
        if not line:
            continue
        if line.startswith("event: "):
            event_name = line[len("event: "):]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: "):])))
    return events


class TestSessionEndpoints:
    def test_create_and_list(self, env):
        r = env["client"].post("/sessions", json={"user_id": "u1"})
        assert r.status_code == 200
        sid = r.json()["session_id"]
        r2 = env["client"].get("/users/u1/sessions")
        assert r2.json()["sessions"][0]["session_id"] == sid
        assert r2.json()["sessions"][0]["default"] is True

    def test_chat_sse_stream(self, env):
        _script_turn(env)
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        with env["client"].stream("POST", "/chat",
                                  json={"user_id": "u1", "session_id": sid, "message": "你好"}) as resp:
            events = _sse_events(resp)
        names = [n for n, _ in events]
        assert names == ["token", "token", "token", "dialogue_done", "final"]
        assert "".join(d for n, d in events if n == "token") == "你好呀"
        final = events[-1][1]
        assert final["risk_state"] == "SAFE"
        assert "planned_skill" not in final

    def test_chat_auto_default_session(self, env):
        _script_turn(env)
        with env["client"].stream("POST", "/chat",
                                  json={"user_id": "u1", "message": "hi"}) as resp:
            events = _sse_events(resp)
        assert events[-1][0] == "final"
        assert len(env["sessions"].list_user_sessions("u1")) == 1

    def test_debug_talks_fields(self, env, monkeypatch):
        monkeypatch.setenv("DEBUG_TALKS", "1")
        _script_turn(env)
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        with env["client"].stream("POST", "/chat",
                                  json={"user_id": "u1", "session_id": sid, "message": "hi"}) as resp:
            events = _sse_events(resp)
        final = events[-1][1]
        assert "planned_skill" in final
        assert "current_therapy" in final
        assert final["intervention_count"] == 0

    def test_history_pagination(self, env):
        _script_turn(env)
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        env["client"].post("/chat", json={"user_id": "u1", "session_id": sid, "message": "a"})
        r = env["client"].get(f"/sessions/{sid}/history", params={"page": 1, "page_size": 1})
        data = r.json()
        assert data["total"] == 2
        assert data["total_pages"] == 2
        assert len(data["items"]) == 1
        assert data["items"][0]["seq"] == 2  # 倒序：最新在前

    def test_profile_and_reset(self, env):
        _script_turn(env)
        sid = env["client"].post("/sessions", json={"user_id": "u1"}).json()["session_id"]
        r = env["client"].get("/users/u1/profile")
        assert r.json()["profile"] == {}
        env["client"].post("/chat", json={"user_id": "u1", "session_id": sid, "message": "a"})
        r = env["client"].post(f"/sessions/{sid}/reset")
        assert r.json()["status"] == "reset_done"
        r = env["client"].get(f"/sessions/{sid}/history")
        assert r.json()["total"] == 0

    def test_missing_session_404(self, env):
        r = env["client"].get("/sessions/ghost/history")
        assert r.status_code == 404
