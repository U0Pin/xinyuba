"""ESO（Emotion Support Orchestrator）单元 + 集成测试。"""

import asyncio
import json


import src.skills  # noqa: F401 — 注册技能
from src.agents.emotion_support import (
    EmotionSupportAction,
    EmotionSupportDecision,
    EmotionSupportOrchestrator,
)
from src.agents.affect_labeling import AffectLabelingEngine
from src.agents.progressive_muscle_relaxation import ProgressiveMuscleRelaxationEngine
from src.core.scheduler import TurnContext
from src.core.state import Orchestration, RiskState, SessionState, TherapyProgress
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client


class TestLifecycleProbes:
    def test_pmr_is_live(self):
        assert EmotionSupportOrchestrator.pmr_is_live(SessionState(pmr={"status": "proposing"}))
        assert EmotionSupportOrchestrator.pmr_is_live(SessionState(pmr={"status": "active"}))
        assert not EmotionSupportOrchestrator.pmr_is_live(SessionState(pmr={"status": "closed"}))
        assert not EmotionSupportOrchestrator.pmr_is_live(SessionState())

    def test_affect_is_live(self):
        assert EmotionSupportOrchestrator.affect_is_live(
            SessionState(affect_labeling={"status": "proposing"}))
        assert not EmotionSupportOrchestrator.affect_is_live(
            SessionState(affect_labeling={"status": "closed"}))
        assert not EmotionSupportOrchestrator.affect_is_live(SessionState())

    def test_active_skill_prefers_pmr(self):
        st = SessionState(pmr={"status": "active"}, affect_labeling={"status": "proposing"})
        assert EmotionSupportOrchestrator.active_skill(st) == "pmr"

    def test_active_skill_affect(self):
        st = SessionState(affect_labeling={"status": "proposing"})
        assert EmotionSupportOrchestrator.active_skill(st) == "affect_labeling"

    def test_active_skill_none(self):
        assert EmotionSupportOrchestrator.active_skill(SessionState()) is None

    def test_eligible_daily(self):
        assert EmotionSupportOrchestrator.eligible(SessionState())

    def test_eligible_blocked_by_crisis(self):
        assert not EmotionSupportOrchestrator.eligible(SessionState(crisis=True))

    def test_eligible_blocked_by_therapy(self):
        assert not EmotionSupportOrchestrator.eligible(
            SessionState(therapy=TherapyProgress(name="CBT")))


class TestSpecPriority:
    def test_pmr_higher_than_affect(self):
        specs = {s.name: s.priority for s in EmotionSupportOrchestrator.SPECS}
        assert specs["pmr"] == 20
        assert specs["affect_labeling"] == 10
        assert specs["pmr"] > specs["affect_labeling"]

    def test_all_specs_exclusive(self):
        assert all(s.exclusive for s in EmotionSupportOrchestrator.SPECS)


# ── fake engine（无 LLM，按脚本返回 step/maybe_plan/run 结果） ──────

class _FakeEngine:
    def __init__(self, step=None, maybe_plan=None, run=None):
        self._step = step
        self._maybe_plan = maybe_plan
        self._run = run if run is not None else {}
        self.step_calls = 0
        self.maybe_plan_calls = 0
        self.run_calls = 0
        self.last_switched = None

    async def step(self, ctx):
        self.step_calls += 1
        return self._step

    async def maybe_plan(self, ctx):
        self.maybe_plan_calls += 1
        return self._maybe_plan

    async def run(self, ctx, *, switched_to_therapy):
        self.run_calls += 1
        self.last_switched = switched_to_therapy
        return self._run


def _ctx(state, message="hi"):
    return TurnContext(
        user_id="u1", session_id="s1", trace_id="t1", message=message,
        state=state, profile={}, summary={}, orchestration=Orchestration(),
    )


class TestPlanDecision:
    def test_pmr_offer_is_start(self):
        pmr = _FakeEngine(step={"kind": "offer", "line": "x"})
        affect = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        ctx = _ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(EmotionSupportAction.START, "pmr", "pmr_turn")
        assert ctx.pmr_turn == {"kind": "offer", "line": "x"}
        assert affect.maybe_plan_calls == 0  # PMR 占槽，affect 未探测

    def test_pmr_beat_is_continue(self):
        pmr = _FakeEngine(step={"kind": "beat", "beat_no": 2})
        affect = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        ctx = _ctx(SessionState(pmr={"status": "active"}))
        d = asyncio.run(eso.plan(ctx))
        assert d.action == EmotionSupportAction.CONTINUE
        assert d.skill == "pmr"

    def test_affect_candidate_is_start(self):
        pmr = _FakeEngine(step=None)  # pmr gate miss
        affect = _FakeEngine(maybe_plan={"candidates": ["委屈", "焦虑"]})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        ctx = _ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(
            EmotionSupportAction.START, "affect_labeling", "affect_candidate")
        assert ctx.affect_labeling_plan == {"candidates": ["委屈", "焦虑"]}

    def test_no_candidate_is_none(self):
        pmr = _FakeEngine(step=None)
        affect = _FakeEngine(maybe_plan=None)
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        ctx = _ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(EmotionSupportAction.NONE, None, "no_candidate")

    def test_blocked_not_daily(self):
        eso = EmotionSupportOrchestrator(pmr_engine=_FakeEngine(), affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(crisis=True))
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(
            EmotionSupportAction.NONE, None, "blocked_not_daily")


class TestRunDecision:
    def test_run_pmr_priority_clears_affect(self):
        pmr = _FakeEngine(run={"pmr": {"status": "active"}})
        affect = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        st = SessionState(pmr={"status": "active"}, affect_labeling={"status": "proposing"})
        ctx = _ctx(st)
        ctx.pmr_turn = {"kind": "beat"}
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.SAFE))
        assert updates == {"pmr": {"status": "active"}, "affect_labeling": None}
        assert affect.run_calls == 0

    def test_run_affect_when_pmr_idle(self):
        pmr = _FakeEngine()
        affect = _FakeEngine(run={"affect_labeling": {"status": "closed"}})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect)
        ctx = _ctx(SessionState())
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.SAFE))
        assert updates == {"affect_labeling": {"status": "closed"}}
        assert pmr.run_calls == 0

    def test_run_supersede_by_high_risk(self):
        pmr = _FakeEngine(run={"pmr": None})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(pmr={"status": "active"}))
        ctx.pmr_turn = {"kind": "beat"}
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.HIGH_RISK))
        assert updates == {}  # 让位，pmr.run 未调用
        assert pmr.run_calls == 0

    def test_run_switch_therapy_clears_pmr(self):
        pmr = _FakeEngine(run={"pmr": None})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(pmr={"status": "active"}))
        ctx.pmr_turn = {"kind": "beat"}
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=True, risk=RiskState.SAFE))
        assert updates == {"pmr": None}
        assert pmr.last_switched is True


PMR_OFFER = json.dumps({
    "should_offer": True, "body_focus": "shoulders", "reason": "身体紧绷",
}, ensure_ascii=False)


def _monkey_env(monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")


class TestIntegration:
    def test_eso_plan_selects_pmr_over_affect(self, tmp_path, monkeypatch):
        """真实 engine：身体紧绷命中 PMR gate → 选 PMR，affect 不探测。"""
        _monkey_env(monkeypatch)
        client, provider, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eso = EmotionSupportOrchestrator(
            pmr_engine=ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
            affect_engine=AffectLabelingEngine(client, session_store=sessions),
            session_store=sessions,
        )
        provider.script_complete("test-model", [PMR_OFFER])
        ctx = _ctx(SessionState(), message="肩膀好紧")
        d = asyncio.run(eso.plan(ctx))
        assert d.action == EmotionSupportAction.START and d.skill == "pmr"
        assert ctx.pmr_turn is not None and ctx.pmr_turn["kind"] == "offer"
        assert ctx.affect_labeling_plan is None  # PMR 占槽，affect 未探测

    def test_eso_plan_pmr_holds_slot_affect_makes_no_llm_call(self, tmp_path, monkeypatch):
        """互斥非空洞断言：消息同时命中 PMR gate 与 affect gate（身体紧绷 + 低区分度情绪），
        PMR 优先占槽 → 仅发出 1 次 LLM 调用（PMR 机会评估），affect 路径零 LLM 调用。"""
        _monkey_env(monkeypatch)
        client, provider, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eso = EmotionSupportOrchestrator(
            pmr_engine=ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
            affect_engine=AffectLabelingEngine(client, session_store=sessions),
            session_store=sessions,
        )
        # 两个 gate 都会命中：这里只脚本化 PMR 的响应；若 affect 也被探测会额外记录一次 LLM 调用。
        provider.script_complete("test-model", [PMR_OFFER])
        ctx = _ctx(SessionState(), message="肩膀好紧，心里好烦")
        assert AffectLabelingEngine._gate(eso.affect_engine, ctx.message)  # 前提：affect gate 确已命中
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(EmotionSupportAction.START, "pmr", "pmr_turn")
        assert ctx.pmr_turn is not None and ctx.pmr_turn["kind"] == "offer"
        assert ctx.affect_labeling_plan is None
        # 精确一次 LLM 调用（PMR 机会评估）；affect 探测会额外 +1，故该断言非空洞地证明互斥。
        assert len(provider.calls) == 1

    def test_eso_plan_no_candidate_is_none(self, tmp_path, monkeypatch):
        """真实 engine：闲聊不命中任何 gate → NONE，零 LLM 调用。"""
        _monkey_env(monkeypatch)
        client, provider, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eso = EmotionSupportOrchestrator(
            pmr_engine=ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
            affect_engine=AffectLabelingEngine(client, session_store=sessions),
            session_store=sessions,
        )
        ctx = _ctx(SessionState(), message="今天天气不错")
        d = asyncio.run(eso.plan(ctx))
        assert d == EmotionSupportDecision(EmotionSupportAction.NONE, None, "no_candidate")
        assert ctx.pmr_turn is None and ctx.affect_labeling_plan is None
        assert provider.calls == []  # gate 未命中，无任何 LLM 调用
