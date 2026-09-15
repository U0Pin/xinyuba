"""Phase 1 测试：store/（session_store、profile_store 与只读流水查询）。"""


import pytest

from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore, current_therapy_transcript


@pytest.fixture
def store(tmp_path):
    return SessionStore(data_root=str(tmp_path))


@pytest.fixture
def profile_store(tmp_path):
    return ProfileStore(data_root=str(tmp_path))


def _flow_entry(role, owner, text, **extra):
    return {"role": role, "owner": owner, "text": text, **extra}


class TestSessionLifecycle:
    def test_create_initializes_artifacts(self, store):
        sid = store.create("user-a")
        assert store.exists(sid)
        state = store.read_state(sid)
        assert state == {"owner": "daily", "crisis": False, "risk_level": "SAFE", "therapy": None}
        assert store.read_summary(sid)["summary"] == []
        assert store.read_orchestration(sid) == {}
        assert store.read_flow_all(sid) == []

    def test_list_user_sessions_and_default(self, store):
        s1 = store.create("user-a")
        s2 = store.create("user-a")
        sessions = store.list_user_sessions("user-a")
        assert [s["session_id"] for s in sessions] == [s1, s2]
        assert sessions[0]["default"] is True
        assert sessions[1]["default"] is False

    def test_user_isolation(self, store):
        store.create("user-a")
        assert store.list_user_sessions("user-b") == []


class TestFlow:
    def test_append_seq_and_tail(self, store):
        sid = store.create("u1")
        e1 = store.append_flow(sid, _flow_entry("user", "daily", "你好"))
        e2 = store.append_flow(sid, _flow_entry("agent", "daily", "你好呀"))
        assert e1["seq"] == 1
        assert e2["seq"] == 2
        assert "ts" in e1
        tail = store.read_flow_tail(sid, 1)
        assert tail[0]["seq"] == 2
        assert len(store.read_flow_tail(sid, 10)) == 2

    def test_range_read(self, store):
        sid = store.create("u1")
        for i in range(5):
            store.append_flow(sid, _flow_entry("user", "daily", f"m{i}"))
        got = store.read_flow_range(sid, 2, 4)
        assert [e["seq"] for e in got] == [2, 3, 4]
        assert [e["seq"] for e in store.read_flow_range(sid, start_seq=4)] == [4, 5]

    def test_append_continues_after_restart(self, store, tmp_path):
        sid = store.create("u1")
        store.append_flow(sid, _flow_entry("user", "daily", "m0"))
        # 模拟重启：新实例指向同一数据根
        store2 = SessionStore(data_root=str(tmp_path))
        e = store2.append_flow(sid, _flow_entry("agent", "daily", "m1"))
        assert e["seq"] == 2


class TestStateArtifacts:
    def test_state_roundtrip(self, store):
        sid = store.create("u1")
        new_state = {"owner": "therapy_cbt", "crisis": False, "risk_level": "LOW_RISK",
                     "therapy": {"name": "CBT", "start_seq": 3, "rounds": 1, "step_index": 0,
                                 "basic_flow_complete": False}}
        store.write_state(sid, new_state)
        assert store.read_state(sid) == new_state

    def test_orchestration_and_summary_roundtrip(self, store):
        sid = store.create("u1")
        orch = {"updated_seq": 2, "assessment": {"fusion": {"index": 0.6}}, "step_judgment": {}}
        store.write_orchestration(sid, orch)
        assert store.read_orchestration(sid) == orch
        summ = {"updated_seq": 2, "token_estimate": 100, "summary": [{"theme": "工作"}]}
        store.write_summary(sid, summ)
        assert store.read_summary(sid) == summ

    def test_reset_clears_session_artifacts(self, store):
        sid = store.create("u1")
        store.append_flow(sid, _flow_entry("user", "daily", "m0"))
        store.write_state(sid, {"owner": "therapy_mi"})
        store.reset(sid)
        assert store.read_flow_all(sid) == []
        assert store.read_state(sid)["owner"] == "daily"
        assert store.read_summary(sid)["summary"] == []
        # 序号从 1 重新开始
        e = store.append_flow(sid, _flow_entry("user", "daily", "m1"))
        assert e["seq"] == 1


class TestProfileStore:
    def test_get_default_empty(self, profile_store):
        assert profile_store.get("nobody") == {}

    def test_save_and_get(self, profile_store):
        profile_store.save("u1", {"attachment_style": "anxious", "core_fears": ["被拒绝"]})
        assert profile_store.get("u1")["attachment_style"] == "anxious"
        assert profile_store.get("u1")["core_fears"] == ["被拒绝"]

    def test_user_isolation(self, profile_store):
        profile_store.save("u1", {"a": 1})
        assert profile_store.get("u2") == {}


class TestFlowQueries:
    """current_therapy_transcript 与常用流水读取的回归测试。"""

    def _seed(self, store, sid):
        for role, owner, text in [
            ("user", "daily", "你好"),
            ("agent", "daily", "你好呀"),
            ("user", "daily", "我最近压力很大"),
            ("agent", "therapy_cbt", "我们来看看这个想法"),
            ("user", "therapy_cbt", "我觉得自己很失败"),
        ]:
            store.append_flow(sid, _flow_entry(role, owner, text))

    def test_recent_context(self, store):
        sid = store.create("u1")
        self._seed(store, sid)
        recent = store.read_flow_tail(sid, 2)
        assert [e["text"] for e in recent] == ["我们来看看这个想法", "我觉得自己很失败"]

    def test_context_range(self, store):
        sid = store.create("u1")
        self._seed(store, sid)
        got = store.read_flow_range(sid, 2, 4)
        assert [e["seq"] for e in got] == [2, 3, 4]

    def test_therapy_transcript(self, store):
        sid = store.create("u1")
        self._seed(store, sid)
        transcript = current_therapy_transcript(store, sid)
        assert [e["owner"] for e in transcript] == ["therapy_cbt", "therapy_cbt"]
        assert [e["text"] for e in transcript] == ["我们来看看这个想法", "我觉得自己很失败"]

    def test_therapy_transcript_empty_when_no_therapy(self, store):
        sid = store.create("u1")
        store.append_flow(sid, _flow_entry("user", "daily", "你好"))
        assert current_therapy_transcript(store, sid) == []
