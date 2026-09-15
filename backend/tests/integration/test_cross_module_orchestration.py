"""Cross-Module Orchestration 回归测试（系统级，按行为断言）。

覆盖 ARCHITECTURE「请求生命周期」下已实现的全局优先级：

    CRISIS > SAFETY > CURRENT THERAPY > PMR > AFFECT LABELING > DAILY

只验证行为（谁获得/让出执行权、state 落盘结果、LLM 记账），不触碰任何
生产代码；所有预期值以当前仓库真实实现为准：

- 危机边界由 `CrisisDetected/CrisisCleared.apply` 在轮边界写 state：
  清 therapy / affect_labeling / pmr，rounds 与原因留痕进 last_therapy；
- 决策线 run_turn 在 risk==CRISIS 时短路（情绪支持整个跳过）；
- Therapy 占用情绪支持槽：SwitchToTherapy.apply 清残留，next turn ESO plan
  直接 blocked_not_daily（engine 零 LLM）；
- ESO `plan()`（对话前探测，读 ctx.state 快照）与 `_decide_run()`（决策后落盘）
  是两个独立的防越权闸口（同轮提问时序，滞后一轮生效为既有设计）。

所有 priority / mutual-exclusion 断言都带「前提确实命中」的守卫
（gate 前置断言 / LLM 调用计数），排除空洞断言（参见 test_emotion_support 教训）。
"""

import asyncio
import json

import pytest

import src.skills  # noqa: F401 — 注册技能
from src.agents.affect_labeling import AffectLabelingEngine
from src.agents.emotion_support import (
    EmotionSupportAction,
    EmotionSupportDecision,
    EmotionSupportOrchestrator,
)
from src.agents.host_agent import HostDialogueAgent
from src.agents.progressive_muscle_relaxation import ProgressiveMuscleRelaxationEngine
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
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler, TurnContext
from src.agents.grounding import GroundingEngine
from src.core.state import Orchestration, RiskState, SessionState
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
SAFETY_CRISIS = json.dumps({"risk_level": "CRISIS", "risk_type": "suicidal_ideation"})
SAFETY_HIGH = json.dumps({"risk_level": "HIGH_RISK", "risk_type": "none"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})

PMR_OFFER = json.dumps({
    "should_offer": True, "body_focus": "shoulders",
    "reason": "身体紧绷，适合先做一次轻放松",
}, ensure_ascii=False)
AFFECT_OFFER = json.dumps({
    "should_offer": True, "candidates": ["委屈", "失望", "焦虑"], "reason": "模糊",
}, ensure_ascii=False)
AFFECT_RESP_OTHER = json.dumps({"kind": "no_answer", "reason": "仍模糊"}, ensure_ascii=False)
JUDGE_STAY = json.dumps({"action": "stay", "target_step": None, "achieved": False, "reason": "未达成"})
INTERVENTION_JSON = json.dumps({
    "technique": {"name": "test_tech", "description": "测试技术说明", "steps": ["第一步", "第二步"]},
    "conversation_goal": "测试对话目标",
    "adaptation": {"tone_adjustment": "温和", "pace_adjustment": "slow", "culture_note": "注意"},
    "contraindications": [],
}, ensure_ascii=False)
DECIDE_CBT = json.dumps({"need_therapy": True, "therapy": "CBT", "reason": "r"}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# 全系统集成环境（全离线 FakeProvider）：Host + DecisionLine(安全/疗法/PMR/Affect)
# ═══════════════════════════════════════════════════════════════════════════════

def _monkey_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10 ** 9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10 ** 9)  # 默认不触发疗法决策
    monkeypatch.setattr(config, "THERAPY_MAX_ROUNDS", 10)


def _full_scheduler(client, sessions, profiles):
    therapy_agents = {
        "CBT": CbtTherapyAgent(client, session_store=sessions),
        "ACT": ActTherapyAgent(client, session_store=sessions),
        "DBT": DbtTherapyAgent(client, session_store=sessions),
        "MI": MiTherapyAgent(client, session_store=sessions),
        "SFBT": SfbtTherapyAgent(client, session_store=sessions),
    }
    return Scheduler(
        dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions),
                                   session_store=sessions),
        decision_line=DecisionLine(
            SafetyAgent(client), session_store=sessions,
            therapy_decider=TherapyDecider(client),
            therapy_agents=therapy_agents,
            pmr_engine=ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
            grounding_engine=GroundingEngine(client, session_store=sessions),
            affect_engine=AffectLabelingEngine(client, session_store=sessions),
        ),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )


@pytest.fixture
def env_full(tmp_path, monkeypatch):
    _monkey_env(monkeypatch, tmp_path)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    return {
        "client": client, "provider": provider, "sink": sink,
        "sessions": sessions, "profiles": profiles,
        "scheduler": _full_scheduler(client, sessions, profiles),
        "pmr": ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
        "affect": AffectLabelingEngine(client, session_store=sessions),
        "grounding": GroundingEngine(client, session_store=sessions),
    }


# ── 回合脚本助手（按各线真实调用顺序：plan → stream → safety → 决策 → 沉淀） ──

def run_turn(scheduler, uid, sid, msg):
    async def _collect():
        async for _ in scheduler.handle_message(uid, sid, msg):
            pass
    asyncio.run(_collect())


def agent_names(sink):
    return {r["agent"] for r in sink.records}


def agent_names_since(sink, start):
    return {r["agent"] for r in sink.records[start:]}


def stream_prompt(provider):
    """最后一次 host 流式 prompt（stream 只有 host 一个来源）。"""
    streams = [c[1] for c in provider.calls if c[0] == "stream"]
    return streams[-1] if streams else ""


def script_daily(env, tokens=("好",), first_model=None):
    """脚本化一轮普通（非编排）回合：stream → safety → [首个 test-model 调用] → profile。

    first_model 是本轮第一个 test-model complete 调用的响应
    （如 PMR/affect opportunity 的 JSON）；None = 没有引擎调用。
    """
    env["provider"].script_stream("test-model", [list(tokens)])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [*( [first_model] if first_model else [] ), PROFILE_NOCHANGE])


def offer_pmr(env, sid, msg="肩膀好紧"):
    script_daily(env, ["嗯"], first_model=PMR_OFFER)
    run_turn(env["scheduler"], "u1", sid, msg)


def offer_affect(env, sid, msg="我好难受，说不上怎么了"):
    script_daily(env, ["嗯"], first_model=AFFECT_OFFER)
    run_turn(env["scheduler"], "u1", sid, msg)


def crisis_turn(env, sid, msg="我不想活了"):
    env["provider"].script_stream("test-model", [["我", "陪", "着"]])
    env["provider"].script_complete("test-cheap", [SAFETY_CRISIS])
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def high_risk_turn(env, sid, msg="我撑不住了，难受得不行"):
    env["provider"].script_stream("test-model", [["我", "在"]])
    env["provider"].script_complete("test-cheap", [SAFETY_HIGH])
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def set_therapy(env, sid, name="CBT"):
    state = env["sessions"].read_state(sid)
    state["owner"] = f"therapy_{name.lower()}"
    state["therapy"] = {"name": name, "start_seq": 0, "rounds": 0,
                        "step_index": -1, "branch": None, "basic_flow_complete": False}
    env["sessions"].write_state(sid, state)


def store_state(env, sid):
    return env["sessions"].read_state(sid)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 5 矩阵（ESO 层，fake engine：确定性、覆盖 CRISIS/HIGH_RISK × PMR/Affect）
# 说明：经 DecisionLine 的 CRISIS 路径在 risk 判定后短路，敏感落盘由信号完成；
# 这类 SUPERSEDE 分支只能在 ESO 编排层触达——这是当前真实的两层防线结构。
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeEngine:
    def __init__(self, step=None, maybe_plan=None, run=None):
        self._step, self._maybe_plan, self._run = step, maybe_plan, run or {}
        self.run_calls = 0
        self.last_switched = None

    async def step(self, ctx):
        return self._step

    async def maybe_plan(self, ctx):
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


def _es_decide_update(state, *, pmr_turn=None, risk=RiskState.SAFE):
    """让 ESO 走完整 run 路径（脚本化 engine），返回 updates。"""
    pmr = _FakeEngine()
    eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
    ctx = _ctx(state)
    ctx.pmr_turn = pmr_turn
    updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=risk))
    return eso, updates


class TestEmotionSupportMatrix:
    def test_pmr_active_crisis_supersede(self):
        """PMR active ↓ CRISIS → SUPERSEDE，engine.run 不被调用。"""
        pmr = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(pmr={"status": "active"}))
        ctx.pmr_turn = {"kind": "beat"}
        d = eso._decide_run(ctx, risk=RiskState.CRISIS)
        assert d == EmotionSupportDecision(
            EmotionSupportAction.SUPERSEDE, "pmr", "blocked_by_higher_priority")
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.CRISIS))
        assert updates == {}
        assert pmr.run_calls == 0

    def test_pmr_candidate_high_risk_supersede(self):
        """PMR 本轮候选 pmr_turn ↓ HIGH_RISK → SUPERSEDE，不落盘。"""
        eso, updates = _es_decide_update(
            SessionState(), pmr_turn={"kind": "offer"}, risk=RiskState.HIGH_RISK)
        assert updates == {}

    def test_affect_active_crisis_supersede(self):
        """Affect active ↓ CRISIS → SUPERSEDE，skill 指向 affect_labeling。"""
        pmr = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(affect_labeling={"status": "proposing"}))
        d = eso._decide_run(ctx, risk=RiskState.CRISIS)
        assert d.action == EmotionSupportAction.SUPERSEDE
        assert d.skill == "affect_labeling"
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=False, risk=RiskState.CRISIS))
        assert updates == {}

    def test_pmr_active_unblocked_same_turn_snapshot(self):
        """ESO 以 ctx 快照判定：快照尚未写盘 therapy 时 PMR 本轮 CONTINUE；
        让位由 _run_pmr(switched=True) 与 SwitchToTherapy.apply 在轮边界完成
        （见 TestTherapyPriority 集成测试）。"""
        pmr = _FakeEngine()
        eso = EmotionSupportOrchestrator(pmr_engine=pmr, affect_engine=_FakeEngine())
        ctx = _ctx(SessionState(pmr={"status": "active"}))
        ctx.pmr_turn = {"kind": "beat"}
        d = eso._decide_run(ctx, risk=RiskState.SAFE)
        assert d.action == EmotionSupportAction.CONTINUE

    def test_affect_engine_clears_on_switch(self):
        """affect 切疗法：让位语义由 engine.run(switched=True) 返回清残留实现
        （ESO 交给 engine 自清，不自己拦下）。"""
        affect = _FakeEngine(run={"affect_labeling": None})
        eso = EmotionSupportOrchestrator(pmr_engine=None, affect_engine=affect)
        ctx = _ctx(SessionState(affect_labeling={"status": "proposing"}))
        updates = asyncio.run(eso.run(ctx, switched_to_therapy=True, risk=RiskState.SAFE))
        assert updates == {"affect_labeling": None}
        assert affect.run_calls == 1 and affect.last_switched is True


# ═══════════════════════════════════════════════════════════════════════════════
# Task 3：Crisis > Emotion Support（全系统集成，SafetyAgent 决策）
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrisisPriority:
    def test_pmr_extractor_blocked_during_crisis(self, env_full):
        """危机中（state.crisis 持久化后）PMR/affect 引擎完全不工作（.plan 零调用）。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)  # 先拿到 PMR proposing，铺垫危机清除后的对照
        assert store_state(env_full, sid)["pmr"]["status"] == "proposing"
        crisis_turn(env_full, sid)
        state = store_state(env_full, sid)
        assert state["crisis"] is True
        assert state["pmr"] is None
        # 危机轮内没有 PMR opportunity 之外的新增引擎调用；本轮也无 affect 调用
        start = len(env_full["sink"].records)
        crisis_turn(env_full, sid, "我还是撑不住")
        assert agent_names_since(env_full["sink"], start) & {
            "pmr_opportunity", "pmr_response",
            "affect_labeling_opportunity", "affect_labeling_response"} == set()

    def test_pmr_active_superseded_by_crisis_no_state_leak(self, env_full):
        """PMR active → CRISIS：state.pmr 落 None，危机解除后不得恢复出旧进行态。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)
        script_daily(env_full, ["好"])  # accept → active（纯代码，无引擎 LLM）
        run_turn(env_full["scheduler"], "u1", sid, "好啊")
        state = store_state(env_full, sid)
        assert state["pmr"]["status"] == "active"
        crisis_turn(env_full, sid)
        assert store_state(env_full, sid)["pmr"] is None

    def test_affect_proposing_superseded_by_crisis(self, env_full):
        """Affect pending proposing ↓ CRISIS → state.affect_labeling 清空 + crisis 生效。"""
        sid = env_full["sessions"].create("u1")
        offer_affect(env_full, sid)
        assert store_state(env_full, sid)["affect_labeling"]["status"] == "proposing"
        start = len(env_full["sink"].records)
        crisis_turn(env_full, sid)
        state = store_state(env_full, sid)
        assert state["crisis"] is True
        assert state["affect_labeling"] is None
        # 危机本轮不需要 respondent 分类器（让位 = 不继续提/不落盘）
        assert "affect_labeling_response" not in agent_names_since(env_full["sink"], start)

    def test_affect_candidate_same_turn_as_crisis_never_persisted(self, env_full):
        """危险消息本身让 PMR/affect 候选当轮失效（同轮提问时序下 offer 可能被说出，
        但落盘被安全裁决拦下：state 永不写入 PMR/Affect）。"""
        assert env_full["pmr"]._gate("肩膀好紧")  # 前提：PMR gate 确已命中
        sid = env_full["sessions"].create("u1")
        env_full["provider"].script_stream("test-model", [["嗯"]])
        env_full["provider"].script_complete("test-model", [PMR_OFFER])
        env_full["provider"].script_complete("test-cheap", [SAFETY_CRISIS])
        env_full["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "肩膀好紧，我真不想活了")
        state = store_state(env_full, sid)
        assert state["crisis"] is True
        assert state["pmr"] is None
        assert state["affect_labeling"] is None

    def test_during_crisis_no_engine_llm_calls(self, env_full):
        """危机期间候选 prompt 依然到达安全线，但 emotion support 引擎零 LLM。"""
        sid = env_full["sessions"].create("u1")
        crisis_turn(env_full, sid, "我觉得活着没什么意思")
        # 命中过 crisis gate 的消息，不应出现优先级较低的 emotion support 调用
        assert "pmr_opportunity" not in agent_names(env_full["sink"])
        assert "affect_labeling_opportunity" not in agent_names(env_full["sink"])


# ═══════════════════════════════════════════════════════════════════════════════
# Task 3.5：HIGH_RISK（SAFETY 高于 Emotion Support 的对应分支）
# ═══════════════════════════════════════════════════════════════════════════════

class TestHighRiskPause:
    def test_high_risk_pauses_live_pmr_without_starting_new(self, env_full):
        """HIGH_RISK（非危机）：PMR 停止推进，且不启动新的 skill（state 不变、无落盘）。"""
        assert env_full["pmr"]._gate("肩膀好紧")  # 前提：PMR gate 命中
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)
        start = len(env_full["sink"].records)
        high_risk_turn(env_full, sid, "肩膀还是好绷着，难受得不行")
        state = store_state(env_full, sid)
        # 当前实现：仅暂停推进（engine step None + state 不变），不清残留、不改 owner
        assert state["pmr"]["status"] == "proposing"
        assert state["risk_level"] == "HIGH_RISK"
        assert "pmr_opportunity" not in agent_names_since(env_full["sink"], start)
        assert "affect_labeling_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_high_risk_recovers_then_pmr_resumes(self, env_full):
        """HIGH_RISK 结束后（含一轮缓冲——风险档位滞后一轮生效，与危机时序一致），
        PMR 引擎立即恢复判定（不会被永久锁死）。"""
        sid = env_full["sessions"].create("u1")
        high_risk_turn(env_full, sid)
        script_daily(env_full, ["好"])  # 安全裁决 SAFE：只解除风险档位快照
        run_turn(env_full["scheduler"], "u1", sid, "缓过来了")
        assert store_state(env_full, sid)["risk_level"] == "SAFE"
        start = len(env_full["sink"].records)
        offer_pmr(env_full, sid, msg="肩膀好紧")
        assert store_state(env_full, sid)["pmr"]["status"] == "proposing"
        assert "pmr_opportunity" in agent_names_since(env_full["sink"], start)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 4：CURRENT THERAPY > Emotion Support
# ═══════════════════════════════════════════════════════════════════════════════

class TestTherapyPriority:
    def test_therapy_active_blocks_pmr_trigger(self, env_full):
        """疗程中 + PMR gate 命中消息：PMR 引擎零调用、state.pmr 不落盘。"""
        sid = env_full["sessions"].create("u1")
        set_therapy(env_full, sid, "CBT")
        assert env_full["pmr"]._gate("肩膀好紧")  # 前提：PMR gate 确已命中
        start = len(env_full["sink"].records)
        script_daily(env_full, ["回"])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_full["provider"].script_complete(
            "test-model", [JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "肩膀好紧，还有点想逃")
        state = store_state(env_full, sid)
        assert state["therapy"]["name"] == "CBT"  # 疗程未被夺走
        assert state["pmr"] is None
        assert "pmr_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_therapy_active_blocks_affect_trigger(self, env_full):
        """疗程中 + affect gate 命中消息：affect 引擎零调用、候选不落盘。"""
        sid = env_full["sessions"].create("u1")
        set_therapy(env_full, sid, "CBT")
        assert AffectLabelingEngine._gate(env_full["affect"], "我很烦，说不上怎么了")
        start = len(env_full["sink"].records)
        env_full["provider"].script_stream("test-model", [["回"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_full["provider"].script_complete(
            "test-model", [JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "我很烦，说不上怎么了")
        state = store_state(env_full, sid)
        assert state["therapy"]["name"] == "CBT"
        assert state["affect_labeling"] is None
        assert "affect_labeling_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_pmr_active_superseded_by_switch_to_therapy(self, env_full, monkeypatch):
        """PMR active ↓ therapy 决策 SwitchToTherapy：轮边界 PMR 被清除，疗程接管。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)
        assert store_state(env_full, sid)["pmr"]["status"] == "proposing"
        # 本轮窗口阈值设为 0：这条消息触发疗法决策（不命中 PMR/affect gate）
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        start = len(env_full["sink"].records)
        env_full["provider"].script_stream("test-model", [["好"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        # test-model 调用次序（PMR proposing 活跃时）：PMR 回应 classifier 先于
        # therapy_decider 发起（classifier 归类回应 → 关闭；decider → SwitchToTherapy），
        # 最后 profile。
        env_full["provider"].script_complete(
            "test-model", [json.dumps({"kind": "negative", "reason": "r"}, ensure_ascii=False),
                           DECIDE_CBT,
                           PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "我最近一直在想工作的事，想找人聊聊")
        state = store_state(env_full, sid)
        assert state["therapy"]["name"] == "CBT"
        assert state["pmr"] is None       # supersede：情绪支持让位 therapy
        assert state["affect_labeling"] is None
        assert "pmr_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_affect_proposing_superseded_by_switch_to_therapy(self, env_full, monkeypatch):
        """Affect proposing ↓ SwitchToTherapy：残留 affect proposing 被清除。"""
        sid = env_full["sessions"].create("u1")
        offer_affect(env_full, sid, msg="我真的特别难受，说不上怎么了")
        assert store_state(env_full, sid)["affect_labeling"]["status"] == "proposing"
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        env_full["provider"].script_stream("test-model", [["好"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        # test-model 调用次序：therapy_decider → affect proposing 回应 classifier → profile
        env_full["provider"].script_complete(
            "test-model", [DECIDE_CBT, AFFECT_RESP_OTHER, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "我一直在想工作的事，想找人聊聊")
        state = store_state(env_full, sid)
        assert state["therapy"]["name"] == "CBT"
        assert state["affect_labeling"] is None  # 被清除， therapy 接管
        assert state["pmr"] is None

    def test_single_writer_bold_signals(self, env_full):
        """supersede 的唯一落盘通道是轮边界信号（_apply_signals）：
        决策线期间不直接持久化（ESO ctx.pmr_turn 只影响本轮对话线）。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)
        snapshot = env_full["sessions"].read_state(sid)
        # plan 刚刚写入存的 proposing 是上一轮的信号应用结果；再无别的写入者
        assert snapshot["pmr"]["status"] == "proposing"


# ═══════════════════════════════════════════════════════════════════════════════
# Task 6：继续（Continuation）
# ═══════════════════════════════════════════════════════════════════════════════

class TestContinuation:
    def test_pmr_accept_then_continue_no_re_offer(self, env_full):
        """PMR proposing → accept 同轮 Beat1（active），下一拍继续不重新 START。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)  # offer → proposing
        # accept 轮：代码短句判定，同轮发 Beat1（turnension; no offer prompt）
        script_daily(env_full, ["好"])
        env_full["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "好啊")
        state = store_state(env_full, sid)
        assert state["pmr"]["status"] == "active"
        assert state["pmr"]["beats_done"] == 1
        # 继续轮（软确认）：纯代码推进，本测试信号由 sink 记账检验——继续轮
        # 之后再无新的 pmr_opportunity（不重提）。
        start = len(env_full["sink"].records)
        script_daily(env_full, ["好"])
        env_full["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "嗯嗯好")
        state = store_state(env_full, sid)
        assert state["pmr"]["beats_done"] == 2
        assert state["pmr"]["status"] == "active"
        # 继续轮无任何新的引擎 LLM 调用（settlement profile 调用总是存在）
        assert "pmr_opportunity" not in agent_names_since(env_full["sink"], start)
        assert "pmr_response" not in agent_names_since(env_full["sink"], start)


    def test_affect_proposing_does_not_re_offer_while_open(self, env_full):
        """Affect 已 proposing（本回复后等待中）→ 下轮不再发起新的提议。"""
        sid = env_full["sessions"].create("u1")
        offer_affect(env_full, sid)
        assert store_state(env_full, sid)["affect_labeling"]["status"] == "proposing"
        start = len(env_full["sink"].records)
        # 用户继续模糊讲述（不回答）→ run() 归类 no_answer → 收尾（不第三次追问）
        env_full["provider"].script_stream("test-model", [["嗯"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_full["provider"].script_complete("test-model", [AFFECT_RESP_OTHER, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "就是很累，一直这样")
        state = store_state(env_full, sid)
        assert state["affect_labeling"]["status"] == "closed"
        # 收尾由 response classifier 归类，而不是再次 opportunity（不重复 START）
        assert "affect_labeling_opportunity" not in agent_names_since(env_full["sink"], start)
        assert "affect_labeling_response" in agent_names_since(env_full["sink"], start)


# ═══════════════════════════════════════════════════════════════════════════════
# Task 9：single-active invariant
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleActiveInvariant:
    def test_pmr_active_affect_candidate_same_turn_pmr_only(self, env_full):
        """PMR active + PMR/affect 双 gate 命中：PMR 独占（affect 引擎零调用）。"""
        sid = env_full["sessions"].create("u1")
        offer_pmr(env_full, sid)
        script_daily(env_full, ["好"])
        env_full["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "好啊")  # accept → active


        start = len(env_full["sink"].records)
        msg = "很烦，说不上怎么了，肩膀也紧绷绷的"
        assert env_full["pmr"]._gate(msg) and AffectLabelingEngine._gate(env_full["affect"], msg)
        script_daily(env_full, ["好"])
        env_full["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["pmr"]["status"] == "active"
        assert state["affect_labeling"] is None
        assert "affect_labeling_opportunity" not in agent_names_since(env_full["sink"], start)


    def test_affect_active_pmr_candidate_pmr_takes_slot(self, env_full):
        """Affect proposing + PMR 候选（PMR 优先）→ PMR 接管，affect 被清。"""
        sid = env_full["sessions"].create("u1")
        offer_affect(env_full, sid)
        assert store_state(env_full, sid)["affect_labeling"]["status"] == "proposing"
        msg = "肩膀好紧，先不管那些了，帮我把身体松一松"
        assert env_full["pmr"]._gate(msg)
        env_full["provider"].script_stream("test-model", [["嗯"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_full["provider"].script_complete("test-model", [PMR_OFFER, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["pmr"]["status"] == "proposing"
        assert state["affect_labeling"] is None  # 被 PMR 抢占


# ═══════════════════════════════════════════════════════════════════════════════
# Task 8：Recovery / Non-Leakage
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecovery:
    def test_crisis_cleared_then_emotion_support_works_again(self, env_full):
        """危机结束后（下一普通轮），情绪支持不会被永久锁死。"""
        sid = env_full["sessions"].create("u1")
        crisis_turn(env_full, sid, "我不想活了")     # 进入危机
        script_daily(env_full, ["好"])               # 危机解除（安全裁决 SAFE）
        run_turn(env_full["scheduler"], "u1", sid, "谢谢你，我缓过来了")
        assert store_state(env_full, sid)["crisis"] is False
        # 恢复后的普通轮：PMR gate 照常走机会评估
        offer_pmr(env_full, sid, msg="肩膀好紧")
        assert store_state(env_full, sid)["pmr"]["status"] == "proposing"

    def test_therapy_end_recovers_emotion_support(self, env_full, monkeypatch):
        """Therapy 结束（round cap）→ 恢复 daily 后情绪支持可再次工作。"""
        sid = env_full["sessions"].create("u1")
        state = store_state(env_full, sid)
        state["owner"] = "therapy_cbt"
        state["therapy"] = {"name": "CBT", "start_seq": 0, "rounds": 9,
                            "step_index": 0, "branch": None, "basic_flow_complete": False}
        env_full["sessions"].write_state(sid, state)
        # 该轮编排后 therapy.rounds=10 → round cap EndTherapy
        env_full["provider"].script_stream("test-model", [["回"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_full["provider"].script_complete(
            "test-model", [JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "继续")
        state = store_state(env_full, sid)
        assert state["therapy"] is None
        assert state["owner"] == "daily"
        assert state["last_therapy"]["ended_reason"] == "round_cap"
        # 恢复 daily：emotion support 重新可用（无旧 therapy 泄漏）
        offer_affect(env_full, sid, msg="我还好，就是有时候很烦，说不上怎么了")
        state = store_state(env_full, sid)
        assert state["affect_labeling"]["status"] == "proposing"
        assert state["therapy"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# Task 7/8（Grounding 扩展）：Grounding × PMR/Affect 互斥 + Crisis/Therapy 优先级
# 全链集成（真实 Host/Safety/decider/三引擎），与上方矩阵同一脚本约定。
# ═══════════════════════════════════════════════════════════════════════════════

GROUNDING_OFFER = json.dumps({"should_offer": True, "reason": "需要回到当下"}, ensure_ascii=False)

AFFECT_OFFER_G = json.dumps({
    "should_offer": True, "candidates": ["委屈", "失望", "焦虑"], "reason": "模糊",
}, ensure_ascii=False)


def script_turn_full(env, tokens=("嗯",), first_model=None, cheap=None):
    """脚本化一整轮：stream → [首个 test-model 调用] → safety → profile。"""
    env["provider"].script_stream("test-model", [list(tokens)])
    env["provider"].script_complete("test-model", [
        *([first_model] if first_model else []),
        SAFETY_SAFE if False else [PROFILE_NOCHANGE] and [],
    ] if False else ([first_model] if first_model else []) + [PROFILE_NOCHANGE])
    env["provider"].script_complete("test-cheap", [cheap or SAFETY_SAFE])


class TestGroundingCrossSkill:
    def test_grounding_trigger_lands_offer_and_state(self, env_full):
        """"脑子乱"命中 grounding gate → offer 落盘 proposing，host prompt 带引导块。"""
        assert env_full["grounding"]._gate("我脑子好乱")  # 前提：grounding gate 确已命中
        sid = env_full["sessions"].create("u1")
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "我脑子好乱")
        state = store_state(env_full, sid)
        assert state["grounding"]["status"] == "proposing"
        assert "grounding_opportunity" in agent_names(env_full["sink"])
        assert "回到当下" in stream_prompt(env_full["provider"])
        assert "情绪命名协助" not in stream_prompt(env_full["provider"])  # 占槽互斥

    def test_grounding_beats_affect_when_gates_both_hit(self, env_full):
        """同轮既想回到当下又有模糊情绪 → Grounding 抢先（PMR/affect 零调用）。"""
        msg = "我脑子特别乱，说不上自己怎么了"
        assert env_full["grounding"]._gate(msg)
        assert env_full["affect"]._gate(msg)
        sid = env_full["sessions"].create("u1")
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["grounding"]["status"] == "proposing"
        assert state["affect_labeling"] is None
        assert "affect_labeling_opportunity" not in agent_names(env_full["sink"])

    def test_pmr_live_blocks_grounding_padding_affordance(self, env_full):
        """PMR active + grounding trigger → PMR 继续占槽（grounding 不探测）。"""
        sid = env_full["sessions"].create("u1")
        # turn1: PMR offer
        script_daily(env_full, ["嗯"], first_model=PMR_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "肩膀好紧")
        assert store_state(env_full, sid)["pmr"]["status"] == "proposing"
        # turn2: accept → active
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, "好啊")
        assert store_state(env_full, sid)["pmr"]["status"] == "active"
        # turn3: grounding trigger（PMR active 中）
        msg = "脑子好乱，还有点飘"
        assert env_full["grounding"]._gate(msg)
        start = len(env_full["sink"].records)
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["pmr"]["status"] == "active"    # PMR 继续
        assert state["grounding"] is None
        assert "grounding_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_grounding_live_affect_candidate_grounding_only(self, env_full):
        """Grounding active + affect trigger → Grounding CONTINUE，affect 不探测。"""
        sid = env_full["sessions"].create("u1")
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "感觉整个人乱掉了")
        assert store_state(env_full, sid)["grounding"]["status"] == "proposing"
        # accept → 第一步（guide visual）
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, "好啊")
        state = store_state(env_full, sid)
        assert state["grounding"]["status"] == "active"
        assert state["grounding"]["step"] == 0
        # 再来一轮模糊情绪消息（affect gate 会命中）→ grounding 继续推进而不是 affect
        start = len(env_full["sink"].records)
        msg = "看着那个杯子，心里还是有点烦，说不清"
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["grounding"]["status"] == "active"    # grounding 占槽
        assert state["grounding"]["step"] == 1             # 推进到 tactile
        assert state["affect_labeling"] is None
        assert "affect_labeling_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_affect_live_grounding_candidate_grounding_takes_slot(self, env_full):
        """Affect proposing + grounding 候选（arrow grounding 优先级高于 affect）→ Grounding 接管。"""
        sid = env_full["sessions"].create("u1")
        offer_affect(env_full, sid)
        assert store_state(env_full, sid)["affect_labeling"]["status"] == "proposing"
        msg = "还是脑子好乱，帮我稳一稳"
        assert env_full["grounding"]._gate(msg)
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, msg)
        state = store_state(env_full, sid)
        assert state["grounding"]["status"] == "proposing"
        assert state["affect_labeling"] is None   # 被 Grounding 抢占


class TestGroundingCrossModule:
    def test_grounding_trigger_yield_crisis_message(self, env_full):
        """"Grounding-gate 消息同时含危机信号 → 完全让位（state 里没有 grounding）。"""
        sid = env_full["sessions"].create("u1")
        assert env_full["grounding"]._gate("脑子乱到不行")  # 前提：grounding gate 平时命中
        # 危机消息：grounding gate 因 CRISIS_CUES 硬排除（前置守卫）
        crisis_msg = "脑子乱到不行，我不想活了"
        from src.skills.affect_lexicon import CRISIS_CUES
        assert any(c in crisis_msg for c in CRISIS_CUES)
        assert not env_full["grounding"]._gate(crisis_msg)
        crisis_turn(env_full, sid, crisis_msg)
        state = store_state(env_full, sid)
        assert state["crisis"] is True
        assert state["grounding"] is None
        assert "grounding_opportunity" not in agent_names(env_full["sink"])

    def test_grounding_active_superseded_by_crisis(self, env_full):
        """Grounding active ↓ CRISIS：轮边界 state.grounding 清空。"""
        sid = env_full["sessions"].create("u1")
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "感觉整个人乱掉了")
        assert store_state(env_full, sid)["grounding"]["status"] == "proposing"
        # accept → active
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, "好啊")
        assert store_state(env_full, sid)["grounding"]["status"] == "active"
        # crisis 轮（不带危机词的消息，由安全裁决）
        crisis_turn(env_full, sid, "我受不了了")
        state = store_state(env_full, sid)
        assert state["crisis"] is True
        assert state["grounding"] is None    # 危机清了 Grounding

    def test_grounding_active_persists_excluding_therapy_answer(self, env_full, monkeypatch):
        """Grounding active ↓ SwitchToTherapy：轮边界 grounding 清空，therapy 接管。"""
        sid = env_full["sessions"].create("u1")
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "感觉整个人乱掉了")
        assert store_state(env_full, sid)["grounding"]["status"] == "proposing"
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        start = len(env_full["sink"].records)
        # 下一轮（grounding proposing 中）：触发疗法决策（消息不再命中 grounding gate）
        env_full["provider"].script_stream("test-model", [["好"]])
        env_full["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        # 顺序：grounding proposing classifier（归类 negative → 关闭）→ therapy_decider → profile
        env_full["provider"].script_complete(
            "test-model", [json.dumps({"kind": "negative", "reason": "r"}, ensure_ascii=False),
                           DECIDE_CBT, PROFILE_NOCHANGE])
        run_turn(env_full["scheduler"], "u1", sid, "我最近在想工作的出路，想找人聊聊")
        state = store_state(env_full, sid)
        assert state["therapy"]["name"] == "CBT"
        assert state["grounding"] is None
        assert "grounding_opportunity" not in agent_names_since(env_full["sink"], start)

    def test_grounding_recovery_after_crisis_clear(self, env_full):
        """Crisis 清除后（lag 一轮 + buffet 轮），grounding 重新可用。"""
        sid = env_full["sessions"].create("u1")
        crisis_turn(env_full, sid, "我不想活了")
        script_daily(env_full, ["好"])
        run_turn(env_full["scheduler"], "u1", sid, "谢谢你，我缓过来了")
        assert store_state(env_full, sid)["crisis"] is False
        start = len(env_full["sink"].records)
        script_daily(env_full, ["嗯"], first_model=GROUNDING_OFFER)
        run_turn(env_full["scheduler"], "u1", sid, "脑子又开始乱了")
        assert store_state(env_full, sid)["grounding"]["status"] == "proposing"
        assert "grounding_opportunity" in agent_names_since(env_full["sink"], start)
