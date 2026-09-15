"""新架构会话持久化测试：SessionStore / ProfileStore（JSONL 流水 + json 产物）。"""

import pytest

from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore


@pytest.fixture
def stores(tmp_path):
    return SessionStore(data_root=str(tmp_path)), ProfileStore(data_root=str(tmp_path))


def _entry(role, owner, text, **extra):
    return {"role": role, "owner": owner, "text": text, **extra}


class TestFlowPersistence:
    def test_round_trip_after_restart(self, stores, tmp_path):
        sessions, _ = stores
        sid = sessions.create("u1")
        sessions.append_flow(sid, _entry("user", "daily", "你好"))
        sessions.append_flow(sid, _entry("agent", "daily", "你好呀"))

        # 模拟重启：新实例指向同一数据根
        sessions2 = SessionStore(data_root=str(tmp_path))
        assert sessions2.exists(sid)
        flow = sessions2.read_flow_all(sid)
        assert [e["text"] for e in flow] == ["你好", "你好呀"]
        assert [e["seq"] for e in flow] == [1, 2]

    def test_get_or_create_semantics(self, stores):
        sessions, _ = stores
        sid = sessions.create("u1")
        assert sessions.exists(sid)
        assert not sessions.exists("ghost")

    def test_load_nonexistent_returns_empty(self, stores):
        sessions, _ = stores
        assert sessions.read_flow_all("ghost") == []
        assert sessions.read_summary("ghost")["summary"] == []


class TestProfilePersistence:
    def test_save_load_round_trip(self, stores, tmp_path):
        sessions, profiles = stores
        profiles.save("u1", {"attachment_style": "anxious", "core_fears": ["被拒绝"]})
        profiles2 = ProfileStore(data_root=str(tmp_path))
        got = profiles2.get("u1")
        assert got["attachment_style"] == "anxious"
        assert got["core_fears"] == ["被拒绝"]

    def test_missing_profile_empty(self, stores):
        _, profiles = stores
        assert profiles.get("nobody") == {}


class TestReset:
    def test_reset_clears_session_keeps_profile(self, stores):
        sessions, profiles = stores
        sid = sessions.create("u1")
        sessions.append_flow(sid, _entry("user", "daily", "m0"))
        sessions.write_state(sid, {"owner": "therapy_mi"})
        profiles.save("u1", {"attachment_style": "secure"})

        sessions.reset(sid)
        assert sessions.read_flow_all(sid) == []
        assert sessions.read_state(sid)["owner"] == "daily"
        assert profiles.get("u1")["attachment_style"] == "secure"
