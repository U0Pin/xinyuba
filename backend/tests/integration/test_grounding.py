"""Grounding（着陆/回到当下）Emotion Support Skill —— 单元 + 集成测试。

覆盖（对应 spec 任务清单）：
- 技能注册 / opportunity parse（失败保守不提议）；
- gate：明确稳定请求 / 失控感 / 脱离当下 / 陷入事件命中；
  身体放松（PMR 域）、模糊情绪（Affect 域）、危机、普通闲聊不命中；
- 同轮提议 / 接受同轮第一步（视觉锚定）/ 拒绝 / 冷却不重提；
- 生命周期：offer → accept → visual → tactile → auditory → check → complete；
- mid-flight 退出（"我不想做了"/"我好多了"）；
- 分类失败默认继续（不卡死）；
- 与 PMR/Affect 的互斥与优先级（grounding 占槽时 affect 不探测、LLM 调用守卫）；
- step() 对 ctx.state 零副作用（单写者纪律回归）。
"""

import asyncio
import json


import src.skills  # noqa: F401 — 注册技能
from src.agents.grounding import GroundingEngine
from src.agents.emotion_support import (
    EmotionSupportAction,
    EmotionSupportOrchestrator,
)
from src.core.scheduler import TurnContext
from src.core.state import Orchestration, RiskState, SessionState
from src.core.skill import skill_registry
from src.utils.config import config
from tests.support.fakes import make_fake_client

GROUNDING_OFFER = json.dumps({"should_offer": True, "reason": "需要回到当下"}, ensure_ascii=False)
GROUNDING_NO_OFFER = json.dumps({"should_offer": False, "reason": "不适合"}, ensure_ascii=False)
RESP_AFFIRM = json.dumps({"kind": "affirmative", "reason": "同意"}, ensure_ascii=False)
RESP_NEG = json.dumps({"kind": "negative", "reason": "拒绝"}, ensure_ascii=False)
RESP_OTHER = json.dumps({"kind": "other", "reason": "模糊"}, ensure_ascii=False)


def _k(kind, reason="r"):
    return json.dumps({"kind": kind, "reason": reason}, ensure_ascii=False)


def _monkey_env(monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")


class _Sessions:
    def next_flow_seq(self, sid):
        return 9  # 任意固定值，cooldown 判定用

    def read_flow_tail(self, sid, n):
        return []


def _engine(client=None, cooldown=999):
    llm = client or _fake_client()[0]
    e = GroundingEngine(llm)
    e.sessions = _Sessions()
    e.cooldown_seqs = cooldown
    return e


def _fake_client():
    from src.core.skill import skill_registry  # noqa: F401 — 已注册技能
    return make_fake_client()


def _ctx(msg="我脑子特别乱", *, g=None, **kw):
    st = SessionState(grounding=g, **kw)
    ctx = TurnContext(
        user_id="u1", session_id="s1", trace_id="t1", message=msg,
        state=st, profile={}, summary={}, orchestration=Orchestration(),
    )
    return ctx


def _scripted_engine(monkeypatch, responses):
    """带 FakeProvider 的 engine（脚本化 LLM 响应按调用次序弹出）。"""
    _monkey_env(monkeypatch)
    client, provider, _ = _fake_client()
    for r in responses:
        provider.script_complete("test-model", [r])
    e = GroundingEngine(client)
    e.sessions = _Sessions()
    e.cooldown_seqs = 999
    return e, provider


class TestSkillRegistration:
    def test_skills_registered(self):
        assert skill_registry.get("grounding_opportunity") is not None
        assert skill_registry.get("grounding_response") is not None

    def test_opportunity_parse(self, monkeypatch):
        _monkey_env(monkeypatch)
        client, provider, _ = _fake_client()
        provider.script_complete("test-model", [GROUNDING_OFFER])
        _engine(client)
        bound = client.bind(agent="grounding_opportunity", user_id="u", session_id="s", trace_id="t")
        skill = skill_registry.get("grounding_opportunity")
        res = asyncio.run(skill.aexecute({"user_text": "我脑子好乱", "history": [], "profile": {}}, {"llm": bound}))
        assert res.success and res.output["should_offer"] is True

    def test_opportunity_fallback_no_offer(self):
        # LLM 失败路径：直接调 skill.fallback（单测）
        skill = skill_registry.get("grounding_opportunity")
        res = skill.fallback({})
        assert res.output == {"should_offer": False, "reason": "opportunity_llm_failed"}


class TestGate:
    """gate 命中测试（真实 engine._gate，词表非硬映射只作预筛）。"""

    def test_gate_positive_triggers(self):
        e = _engine()
        for msg in [
            "我想先冷静一下", "我现在需要缓一缓", "脑子特别乱", "感觉整个人乱掉了",
            "感觉自己飘着", "感觉周围不太真实", "我一直在想刚才那件事",
            "我一直陷在这个情绪里面",
        ]:
            assert e._gate(msg), msg

    def test_gate_excludes_crisis(self):
        e = _engine()
        assert not e._gate("我不想活了")
        assert not e._gate("脑子很乱，我不想活了")

    def test_gate_negative_triggers(self):
        """普通情绪/闲聊/身体放松不应进 Grounding（分别给 Affect/PMR 或日常）。"""
        e = _engine()
        for msg in ["我今天很焦虑", "我最近压力很大", "我很难过", "我和朋友吵架了",
                    "我觉得自己很失败", "肩膀好紧", "帮我把身体放松一下",
                    "今天天气不错", "我吃了顿火锅"]:
            assert not e._gate(msg), msg

    def test_gate_second_round_lexicon_additions(self):
        """第二轮路由评估（2026-09-12）补录 cue 的回归：急性涌动/失实感/侵入画面/
        拉回请求命中；平静的眼前描述仍不命中（over-intervention 防线）。"""
        e = _engine()
        for msg in [
            "焦虑一下子涌上来，抓住我一下。",                      # OVERWHELM: 涌上来
            "情绪浪头正在打过来，给我一个能立刻用的一手。",         # OVERWHELM: 浪头/能立刻用
            "恍惚得厉害，周围的一切都像隔了一层玻璃。",             # DETACHMENT: 恍惚/隔了一层玻璃
            "我盯着天花板发怔，好像又回到那个下午了。",             # DETACHMENT: 发怔
            "帮我把注意力拉回到现在这间屋子，我快飘走了。",         # STABILIZE: 拉回；DETACH: 飘走
            "脑子里全是车祸那个画面，一直闪回来。",                 # RUMINATION: 闪回来
            "一闭眼全是那间病房，我不想待在里面。",                 # RUMINATION: 一闭眼全是
            "闭上眼，那些争论的场面一遍遍循环，越想心跳越快。",     # RUMINATION: 一遍遍循环
            "我就在山风里，脚下是实地，我把自己收回来一点。",       # STABILIZE: 收回来
            "我想像 DBT 里说的那样，坐在河边看着水流，就守在这儿。",# STABILIZE: 守在这儿
        ]:
            assert e._gate(msg), msg
        # 平静的 present-perception 描述（BND-01 裁定）不是 grounding 诉求
        assert not e._gate("我现在就坐在这儿，看见桌上有一杯水和一部手机，周围很安静。")

    def test_gate_third_round_therapy_eval_additions(self):
        """第三轮 therapy 评估（2026-09-12）补录 cue 的回归（ESB-03/05/07）：
        急性失能功能恢复请求 / 内攻击外部稳定请求 / 惊醒后解离相邻麻木 +
        here-and-now 锚定祈使命中；对应 adversarial 变体不命中。"""
        e = _engine()
        for msg in [
            "心跳快得反胃了，我需要先能说话，别让我做 introspection。",  # STABILIZE:
            "今天脑子里那个失败者的声音特别大声，我需要有人帮我把它骂回去。",  # 外部稳定
            "我半夜又惊醒了整个人是麻的，先让我回到'在床上'这个事实里。",  # DETACH+STABILIZE
        ]:
            assert e._gate(msg), msg
        # adversarial negatives：cue 不在第三人称/普通叙述/身体描写上误触发
        for msg in [
            "这话我没打算骂回去的意思",            # 「骂回去」脱离完整请求短句
            "手脚都是麻的",                        # 单纯体感描写，非解离相邻惊醒
            "先让我回到公司再说",                  # 普通去处叙述，非 here-and-now 锚定
        ]:
            assert not e._gate(msg), msg


class TestLifecycle:
    def test_offer_turn(self, monkeypatch):
        e, provider = _scripted_engine(monkeypatch, [GROUNDING_OFFER])
        ctx = _ctx("我脑子好乱")
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "offer"
        assert turn["_next_state"]["status"] == "proposing"
        # ctx.state 未被修改（单写者）
        assert ctx.state.grounding is None

    def test_accept_same_turn_starts_visual(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        g = {"status": "proposing", "step": 0, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("好啊", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "guide" and turn["step"] == 0 and turn["step_name"] == "visual"
        assert turn["_next_state"]["status"] == "active"

    def test_decline_closes(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        g = {"status": "proposing", "step": 0, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("算了，不用了", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "ack_close"
        assert turn["_next_state"]["close_reason"] == "declined"
        assert turn["_next_state"]["status"] == "closed"

    def test_step_progression_visual_tactile_auditory(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        # step=0 是当前正在引导的一步名；下一轮应推进到下一步：
        for step, want_name in [(0, "tactile"), (1, "auditory")]:
            g = {"status": "active", "step": step, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
            ctx = _ctx("嗯", g=g)
            turn = asyncio.run(e.step(ctx))
            assert turn["kind"] == "guide" and turn["step_name"] == want_name, step
            assert turn["_next_state"]["step"] == step + 1
        # step=2 (auditory) 的下一轮 → check 步
        g = {"status": "active", "step": 2, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        turn = asyncio.run(e.step(_ctx("嗯", g=g)))
        assert turn["kind"] == "check" and turn["step"] == 3

    def test_check_then_complete(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        # auditory 完成 → check
        g2 = {"status": "active", "step": 2, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("嗯", g=g2)
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "check" and turn["step"] == 3
        # 用户回应 check → completed
        g3 = turn["_next_state"]
        ctx2 = _ctx("嗯，落地点了", g=g3)
        turn2 = asyncio.run(e.step(ctx2))
        assert turn2["kind"] == "closing_feedback"
        assert turn2["_next_state"]["status"] == "closed"
        assert turn2["_next_state"]["close_reason"] == "completed"

    def test_exit_midpractice(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        g = {"status": "active", "step": 1, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("我不想做了", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "ack_close" and turn["reason"] == "exited"

    def test_felt_better_closes(self, monkeypatch):
        e, _ = _scripted_engine(monkeypatch, [])
        g = {"status": "active", "step": 2, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("我现在好多了", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn["reason"] == "felt_better" and turn["_next_state"]["status"] == "closed"

    def test_ambiguous_active_response_classifies_and_advances(self, monkeypatch):
        e, provider = _scripted_engine(monkeypatch, [RESP_AFFIRM])
        g = {"status": "active", "step": 0, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        ctx = _ctx("那个杯子挺好看的，就是我这不知道干嘛", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn["kind"] == "guide" and turn["step"] == 1  # classifier → affirmative → 推进

    def test_cooldown_suppresses_reoffer(self, monkeypatch):
        """closed 后冷却期内不会再次提议（dup suppression）。"""
        class _Sess(_Sessions):
            def next_flow_seq(self, sid):
                return 3  # 冷却窗口内（closed at 1）
        _monkey_env(monkeypatch)
        client, provider, _ = _fake_client()
        e = GroundingEngine(client)
        e.sessions = _Sess()
        e.cooldown_seqs = 6
        g = {"status": "closed", "step": 0, "trigger_seq": 1, "last_seq": 1,
             "close_reason": "completed"}
        ctx = _ctx("我脑子好乱", g=g)
        turn = asyncio.run(e.step(ctx))
        assert turn is None  # 冷却内不再打扰
        assert provider.calls == []  # 甚至不再调 opportunity LLM


class TestSafetyGates:
    def test_crisis_message_yields_safety(self):
        e = _engine()
        g = {"status": "active", "step": 0, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        turn = asyncio.run(e.step(_ctx("其实我不想活了", g=g)))
        assert turn is None  # 练习中出危机 → 不推进不清（上层信号负责清理）

    def test_high_risk_pauses(self):
        e = _engine()
        g = {"status": "active", "step": 0, "trigger_seq": 1, "last_seq": 1, "close_reason": None}
        turn = asyncio.run(e.step(_ctx("嗯", g=g, risk_level=RiskState.HIGH_RISK)))
        assert turn is None

    def test_therapy_blocks(self):
        from src.core.state import TherapyProgress
        e = _engine()
        turn = asyncio.run(e.step(_ctx("我脑子好乱", therapy=TherapyProgress(name="CBT"))))
        assert turn is None

    def test_crisis_state_blocks(self):
        e = _engine()
        turn = asyncio.run(e.step(_ctx("我脑子好乱", crisis=True)))
        assert turn is None


class TestRunPersistence:
    def test_run_persists_next_state(self):
        e = _engine()
        next_state = {"status": "proposing", "step": 0, "trigger_seq": 1,
                      "last_seq": 1, "close_reason": None}
        ctx = _ctx()
        ctx.grounding_turn = {"kind": "offer", "line": "x", "_next_state": next_state}
        updates = asyncio.run(e.run(ctx, switched_to_therapy=False))
        assert updates == {"grounding": next_state}

    def test_run_switched_clears(self):
        e = _engine()
        ctx = _ctx(g={"status": "active", "step": 0, "trigger_seq": 1,
                      "last_seq": 1, "close_reason": None})
        updates = asyncio.run(e.run(ctx, switched_to_therapy=True))
        assert updates == {"grounding": None}

    def test_run_noop_when_no_turn(self):
        e = _engine()
        ctx = _ctx()
        updates = asyncio.run(e.run(ctx, switched_to_therapy=False))
        assert updates == {}


# ═══════════════════════════════════════════════════════════════════════════════
# ESO 集成（fake engine，确定性验证：互斥、优先级、占槽）
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeEngine:
    def __init__(self, step=None, maybe_plan=None, run=None):
        self._step, self._maybe_plan, self._run = step, maybe_plan, run or {}
        self.run_calls = 0
        self.step_calls = 0
        self.maybe_plan_calls = 0

    async def step(self, ctx):
        self.step_calls += 1
        return self._step

    async def maybe_plan(self, ctx):
        self.maybe_plan_calls += 1
        return self._maybe_plan

    async def run(self, ctx, *, switched_to_therapy):
        self.run_calls += 1
        return self._run


def _eso_ctx(state, message="hi"):
    from src.core.scheduler import TurnContext as TC
    from src.core.state import Orchestration as Orch
    return TC(user_id="u1", session_id="s1", trace_id="t1", message=message,
              state=state, profile={}, summary={}, orchestration=Orch())


class TestESOPlanPriority:
    def test_grounding_offer_is_start(self):
        pmr = _FakeEngine(step=None)
        grounding = _FakeEngine(step={"kind": "offer", "line": "x"})
        affect = _FakeEngine(maybe_plan=None)
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=grounding)
        ctx = _eso_ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert (d.action, d.skill) == (EmotionSupportAction.START, "grounding")
        assert ctx.grounding_turn == {"kind": "offer", "line": "x"}
        assert affect.maybe_plan_calls == 0  # grounding 占槽 → affect 不探测
        assert pmr.step_calls == 1  # PMR 仍先探测（gate miss）

    def test_pmr_live_beats_grounding_candidate(self):
        pmr = _FakeEngine(step=None)
        grounding = _FakeEngine(step={"kind": "offer", "line": "x"})
        affect = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=grounding)
        ctx = _eso_ctx(SessionState(pmr={"status": "proposing"}), message="脑子乱，想稳下来")
        d = asyncio.run(eso.plan(ctx))
        assert d.skill == "pmr" and d.action == EmotionSupportAction.CONTINUE
        assert grounding.step_calls == 0  # PMR 占槽 → grounding 不探测

    def test_grounding_live_blocks_affect(self):
        pmr = _FakeEngine(step=None)
        grounding = _FakeEngine(step=None)
        affect = _FakeEngine(maybe_plan={"candidates": ["委屈", "失落"]})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=grounding)
        ctx = _eso_ctx(SessionState(grounding={"status": "active", "step": 1}))
        d = asyncio.run(eso.plan(ctx))
        assert d.skill == "grounding" and d.action == EmotionSupportAction.CONTINUE
        assert affect.maybe_plan_calls == 0  # 不重复探测 affect
        assert eso.affect_engine._maybe_plan  # 纯守卫（若探测会写 plan）

    def test_affect_runs_when_both_idle(self):
        pmr = _FakeEngine(step=None)
        grounding = _FakeEngine(step=None)
        affect = _FakeEngine(maybe_plan={"candidates": ["委屈", "失落"]})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=grounding)
        ctx = _eso_ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert d.skill == "affect_labeling"

    def test_no_engine_no_candidate(self):
        eso = EmotionSupportOrchestrator()
        ctx = _eso_ctx(SessionState())
        d = asyncio.run(eso.plan(ctx))
        assert d.action == EmotionSupportAction.NONE


class TestESODecideRun:
    def test_grounding_active_high_risk_supersede(self):
        pmr = _FakeEngine()
        g = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine(),
                                         grounding_engine=g)
        ctx = _eso_ctx(SessionState(grounding={"status": "active", "step": 1}))
        ctx.pmr_turn = None
        ctx.grounding_turn = {"kind": "guide", "step": 1}
        upd = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.HIGH_RISK))
        assert upd == {}
        assert g.run_calls == 0

    def test_grounding_continue_runs(self):
        pmr = _FakeEngine()
        affect = _FakeEngine()
        g = _FakeEngine(run={"grounding": {"status": "active", "step": 1}})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=g)
        ctx = _eso_ctx(SessionState())
        ctx.grounding_turn = {"kind": "guide", "step": 0}
        upd = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.SAFE))
        assert upd == {"grounding": {"status": "active", "step": 1}}
        assert g.run_calls == 1

    def test_grounding_clears_residue_affect(self):
        pmr = _FakeEngine()
        affect = _FakeEngine()
        g = _FakeEngine(run={})
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=g)
        ctx = _eso_ctx(SessionState(affect_labeling={"status": "proposing"}))
        ctx.grounding_turn = {"kind": "guide", "step": 0}
        upd = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.SAFE))
        assert upd == {"affect_labeling": None}
        assert affect.run_calls == 0

    def test_pmr_run_clears_grounding_residue(self):
        pmr = _FakeEngine(run={"pmr": {"status": "active"}})
        g = _FakeEngine()
        affect = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=affect,
                                         grounding_engine=g)
        st = SessionState(pmr={"status": "active"},
                          grounding={"status": "proposing", "step": 0},
                          affect_labeling={"status": "proposing"})
        ctx = _eso_ctx(st)
        ctx.pmr_turn = {"kind": "beat"}
        upd = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.SAFE))
        assert upd == {"pmr": {"status": "active"}, "grounding": None,
                       "affect_labeling": None}

    def test_spec_priority_order(self):
        specs = {s.name: s.priority for s in EmotionSupportOrchestrator.SPECS}
        assert specs["pmr"] == 20 > specs["grounding"] == 15 > specs["affect_labeling"] == 10
