"""PMR（渐进式肌肉放松）Emotion Support Skill —— 单元 + 集成测试。

覆盖（规格 §30 清单 + 已确认决策点）：
- 触发预筛 gate：身体紧绷/想放松命中；闲聊/胸肺体感/危机/纯情绪一律不命中
  （无 keyword→PMR 硬映射：命中只是候选，是否提议由 opportunity LLM 决定）；
- 技能注册 / parse / fallback（机会保守不提议、回应失败回 other）；
- 同轮提议 / 接受同轮 Beat1（🔴 accept 时序 MUST）/ 拒绝 / 冷却不重提；
- 软确认不机械阻塞（短软确认 → 纯代码继续、0 classifier）；模糊回复经 classifier
  继续不阻塞；classifier 失败默认继续；
- 全流程 happy path 至 completed（3 区域 × 2 拍 + 自然收尾）；
- 动作中 discomfort 关闭、不能用力 → imaginal、中途 stop；
- topic_shift 判定（决策 6）：完全转向 → 关闭；先回应本拍再顺带提新话题 → 继续；
- 优先级/互斥：PMR beats Affect 同轮、PMR supersede live affect proposing、
  affect 在 pmr 空闲时照常；crisis / 切疗法清 pmr；
- 🔴 step() 对 ctx.state 零副作用（代码纪律 + 回归断言）；
- pmr_engine=None → 运行时零 PMR 影响（仅 to_dict 多出固定 "pmr": null 键）。
"""

import asyncio
import json

import pytest

import src.skills  # noqa: F401 — 注册技能
from src.agents.affect_labeling import AffectLabelingEngine
from src.agents.host_agent import HostDialogueAgent
from src.agents.progressive_muscle_relaxation import (
    ProgressiveMuscleRelaxationEngine,
    _lead_sentence,
)
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
from src.core.signals import CrisisDetected, SwitchToTherapy
from src.core.skill import SkillType, skill_registry
from src.core.state import Orchestration, SessionState
from src.skills import pmr_lexicon as lexicon
from src.skills.pmr import PMR_BODY_ORDER
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
SAFETY_CRISIS = json.dumps({"risk_level": "CRISIS", "risk_type": "suicidal_ideation"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})

PMR_OFFER = json.dumps({
    "should_offer": True, "body_focus": "shoulders",
    "reason": "身体紧绷，适合先做一次轻放松",
}, ensure_ascii=False)
PMR_OFFER_NO_FOCUS = json.dumps({
    "should_offer": True, "body_focus": "general",
    "reason": "整体紧绷",
}, ensure_ascii=False)
PMR_NO_OFFER = json.dumps({
    "should_offer": False, "body_focus": "general", "reason": "此刻不适合",
}, ensure_ascii=False)
PMR_OFFER_HANDS = json.dumps({
    "should_offer": True, "body_focus": "hands", "reason": "手紧",
}, ensure_ascii=False)
PMR_OFFER_FACE = json.dumps({
    "should_offer": True, "body_focus": "face", "reason": "脸僵",
}, ensure_ascii=False)


def _k(kind, reason="r"):
    return json.dumps({"kind": kind, "reason": reason}, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════════════
# env fixtures（全离线 FakeProvider）
# ═══════════════════════════════════════════════════════════════════════════════

def _monkey_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10 ** 9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10 ** 9)
    monkeypatch.setattr(config, "THERAPY_MAX_ROUNDS", 10)


def _scheduler(client, sessions, profiles, *, with_affect: bool):
    therapy_agents = {
        "CBT": CbtTherapyAgent(client, session_store=sessions),
        "ACT": ActTherapyAgent(client, session_store=sessions),
        "DBT": DbtTherapyAgent(client, session_store=sessions),
        "MI": MiTherapyAgent(client, session_store=sessions),
        "SFBT": SfbtTherapyAgent(client, session_store=sessions),
    }
    kwargs = dict(
        session_store=sessions,
        therapy_decider=TherapyDecider(client),
        therapy_agents=therapy_agents,
        pmr_engine=ProgressiveMuscleRelaxationEngine(client, session_store=sessions),
    )
    if with_affect:
        kwargs["affect_engine"] = AffectLabelingEngine(client, session_store=sessions)
    return Scheduler(
        dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions),
                                   session_store=sessions),
        decision_line=DecisionLine(SafetyAgent(client), **kwargs),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    """带 pmr_engine 的 Scheduler 集成环境（pmr only）。"""
    _monkey_env(monkeypatch, tmp_path)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    scheduler = _scheduler(client, sessions, profiles, with_affect=False)
    return {"client": client, "provider": provider, "sink": sink,
            "sessions": sessions, "profiles": profiles, "scheduler": scheduler}


@pytest.fixture
def env_both(tmp_path, monkeypatch):
    """pmr_engine + affect_engine 都装（互斥/优先级测试）。"""
    _monkey_env(monkeypatch, tmp_path)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
    scheduler = _scheduler(client, sessions, profiles, with_affect=True)
    return {"client": client, "provider": provider, "sink": sink,
            "sessions": sessions, "profiles": profiles, "scheduler": scheduler}


def run_turn(scheduler, uid, sid, msg):
    async def _collect():
        async for _ in scheduler.handle_message(uid, sid, msg):
            pass
    asyncio.run(_collect())


def agent_names(sink):
    return {r["agent"] for r in sink.records}


def agent_names_since(sink, start):
    """自 sink 记录下标 start 以来新增的 agent（跨轮记账不串台）。"""
    return {r["agent"] for r in sink.records[start:]}


def stream_prompt(provider):
    """本会话最后一次 host 流式 prompt（stream 只有 host 一个来源）。"""
    streams = [c[1] for c in provider.calls if c[0] == "stream"]
    return streams[-1] if streams else ""


def _offer_pmr(env, sid, msg="肩膀好紧", offer=PMR_OFFER):
    env["provider"].script_stream("test-model", [["嗯"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [offer, PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def _code_turn(env, sid, msg):
    """纯代码轮（accept/继续/stop/discomfort/离开等）：无 PMR LLM、无 classifier。"""
    env["provider"].script_stream("test-model", [["好"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def _classify_turn(env, sid, msg, kind_json):
    env["provider"].script_stream("test-model", [["好"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [kind_json, PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def _pmr_state(sessions, sid):
    return (sessions.read_state(sid) or {}).get("pmr")


# ═══════════════════════════════════════════════════════════════════════════════
# Engine 零副作用回归驱动（对状态快照断言，不经 Scheduler）
# ═══════════════════════════════════════════════════════════════════════════════

def _engine_ctx(sessions, sid, msg, pmr_seed=None):
    st = SessionState.from_dict(sessions.read_state(sid))
    if pmr_seed is not None:
        st.pmr = pmr_seed
    return TurnContext(
        user_id="u1", session_id=sid, trace_id=f"t-{len(msg)}", message=msg,
        state=st, profile={}, summary={}, orchestration=Orchestration(),
        pmr_turn=None,
    ), st


def _active_seed(*, part_index=0, current_part="shoulders", phase="tension",
                 beats_done=1, parts_completed=0, mode="gentle", closing=False):
    return {
        "status": "active", "mode": mode, "start_seq": 1,
        "body_parts": list(PMR_BODY_ORDER), "part_index": part_index,
        "current_part": current_part, "phase": phase, "beats_done": beats_done,
        "parts_completed": parts_completed, "closing": closing,
    }


def _eng_turn(eng, sessions, sid, msg, pmr_seed=None):
    """对 engine 跑一轮 step()+run()，校验 step 零副作用，返回 (pmr_turn, state_updates)。"""
    async def go():
        ctx, st = _engine_ctx(sessions, sid, msg, pmr_seed)
        before = st.to_dict()
        ctx.pmr_turn = await eng.step(ctx)
        assert st.to_dict() == before, "step() 不得改动 ctx.state 快照（零副作用硬约束）"
        updates = await eng.run(ctx, switched_to_therapy=False)
        return ctx.pmr_turn, updates
    return asyncio.run(go())


def _apply(sessions, sid, updates):
    st = SessionState.from_dict(sessions.read_state(sid))
    for k, v in (updates or {}).items():
        setattr(st, k, v)
    return st.pmr  # 返回新 pmr（供后续 seed）


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 触发预筛 gate（纯代码，非 keyword→PMR 映射）
# ═══════════════════════════════════════════════════════════════════════════════

class TestTriggerGate:
    def _eng(self):
        return ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)

    @pytest.mark.parametrize("msg", [
        "肩膀好紧",
        "我肩膀紧绷绷的",
        "浑身绷着，放松不下来",
        "整个人都僵住了",
        "帮我放松一下身体",
        "能不能带我把身体松一松",
        "我想做个肌肉放松练习",
        # 第二轮路由评估（2026-09-12）补录回归：放松请求与「松+身体锚点」组合变体
        "能不能带我做个把肩膀松下来的练习？",   # RELAXATION: 松+肩（release-marker 组合）
        "我需要一点能让我现在就放松下来的练习。",# RELAXATION: 放松下来的练习
        "放松身体的那套练习，能带我试一下吗？",  # RELAXATION: 放松身体（点名请求）
        "情绪最汹涌的时候，肩膀和后颈一路僵到底，帮我把身体松一松。",
    ])
    def test_body_tension_or_relax_request_gates_in(self, msg):
        assert self._eng()._gate(msg) is True

    @pytest.mark.parametrize("msg", [
        "今天天气不错",
        "你叫什么名字",
        "我明天要考试，好紧张",          # 情绪/方案，不是身体紧绷
        "我很烦，说不上为什么",           # 纯情绪 → affect 的事，不是 PMR
        "我很难受，只想哭",
    ])
    def test_casual_emotion_or_solution_gates_out(self, msg):
        assert self._eng()._gate(msg) is False

    @pytest.mark.parametrize("msg", [
        "我胸闷，心慌",
        "胸口闷，喘不上气",
        "心悸，心跳很快",
    ])
    def test_chest_cardiac_never_gates_in(self, msg):
        """PMR 不碰心肺区：胸闷/心慌既是硬排除，也不当紧绷线索。"""
        assert self._eng()._gate(msg) is False

    @pytest.mark.parametrize("msg", [
        "我不想活了，肩膀也好紧",
        "我想伤害自己",
    ])
    def test_crisis_never_gates_in(self, msg):
        assert self._eng()._gate(msg) is False

    def test_gate_chest_tight_is_cardiac_exclusion(self):
        assert self._eng()._gate("胸口紧") is False

    def test_gate_emotional_heart_tight_not_tension(self):
        assert self._eng()._gate("心里紧") is False

    def test_gate_back_hard_combination(self):
        assert self._eng()._gate("背很硬") is True

    def test_gate_cardiac_exclusion_outranks_release_request(self):
        """第二轮评估裁定 PMR-11：心跳加速/胸口发紧的心肺体感命中硬排除时，
        即便句尾带「让身体松开」的释放请求也不进 gate（PMR 不碰心肺区）。"""
        assert self._eng()._gate(
            "一说话就心跳加速，喉咙到胸口都发紧，给我个让身体松开的主意。"
        ) is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 技能注册 / parse / fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkills:
    def test_opportunity_registered_as_emotion_support(self):
        s = skill_registry.get("pmr_opportunity")
        assert s is not None and s.skill_type == SkillType.EMOTION_SUPPORT

    def test_response_registered_as_emotion_support(self):
        s = skill_registry.get("pmr_response")
        assert s is not None and s.skill_type == SkillType.EMOTION_SUPPORT

    def test_opportunity_parse_keeps_valid_focus(self):
        s = skill_registry.get("pmr_opportunity")
        res = s.parse_output({"should_offer": True, "body_focus": "hands", "reason": "r"},
                             {"user_text": "x"})
        assert res.success and res.output["should_offer"] is True
        assert res.output["body_focus"] == "hands"

    def test_opportunity_parse_invalid_focus_falls_back_general(self):
        s = skill_registry.get("pmr_opportunity")
        res = s.parse_output({"should_offer": True, "body_focus": "legs", "reason": "r"},
                             {"user_text": "x"})
        assert res.output["body_focus"] == "general"

    def test_opportunity_should_offer_false_passthrough(self):
        s = skill_registry.get("pmr_opportunity")
        res = s.parse_output({"should_offer": False, "reason": "no"}, {"user_text": "x"})
        assert res.output["should_offer"] is False

    def test_opportunity_fallback_is_conservative(self):
        s = skill_registry.get("pmr_opportunity")
        res = s.fallback({"user_text": "x"})
        assert res.success and res.output["should_offer"] is False

    def test_response_parse_invalid_kind_defaults_other(self):
        s = skill_registry.get("pmr_response")
        res = s.parse_output({"kind": "bogus"}, {"user_text": "x", "situation": "s"})
        assert res.output["kind"] == "other"

    def test_response_fallback_returns_other(self):
        """classifier 失败 → other；active 阶段由引擎映射为默认继续（不阻塞）。"""
        s = skill_registry.get("pmr_response")
        res = s.fallback({"user_text": "x", "situation": "s"})
        assert res.output["kind"] == "other"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 同轮提议 / 接受 / 拒绝 / 冷却
# ═══════════════════════════════════════════════════════════════════════════════

class TestOfferAndAccept:
    def test_body_tension_offers_same_turn(self, env):
        """身体紧绷 → 本轮对话线带出邀请（同轮提问时序），状态落盘 proposing。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr is not None and pmr["status"] == "proposing"
        assert pmr["current_part"] == "shoulders"
        assert pmr["body_parts"] == list(PMR_BODY_ORDER)
        prompt = stream_prompt(env["provider"])
        assert "身体放松支持" in prompt and "要不要" in prompt
        assert "pmr_opportunity" in agent_names(env["sink"])
        assert "## 情绪命名协助" not in prompt  # affect 块未与本轮 PMR 块并存

    def test_casual_chat_never_offers(self, env):
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "今天天气不错")
        assert _pmr_state(env["sessions"], sid) is None
        assert "pmr_opportunity" not in agent_names(env["sink"])

    def test_chest_cardiac_never_offers(self, env):
        """胸闷/心慌：PMR 连机会 LLM 都不跑。"""
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "我胸口闷，有点喘不上气")
        assert _pmr_state(env["sessions"], sid) is None
        assert "pmr_opportunity" not in agent_names(env["sink"])

    def test_opportunity_llm_says_no_stays_idle(self, env):
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["嗯"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PMR_NO_OFFER, PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "肩膀好紧")
        assert _pmr_state(env["sessions"], sid) is None
        prompt = stream_prompt(env["provider"])
        assert "身体放松支持" not in prompt

    def test_accept_starts_beat1_in_same_turn(self, env):
        """🔴 接受时序 MUST：『好啊』的同一轮直接说第一区域 Beat 1，不隔轮。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        # 接受 → 纯代码短句，无 classifier、无额外 PMR LLM
        _code_turn(env, sid, "好啊")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr["phase"] == "tension"
        assert pmr["beats_done"] == 1
        assert pmr["current_part"] == "shoulders"
        assert pmr["closing"] is False
        prompt = stream_prompt(env["provider"])
        assert "我们先从肩膀开始" in prompt          # Beat1（prepare+tension 合并）已出口
        assert "## 情绪命名协助" not in prompt
        assert "pmr_response" not in agent_names(env["sink"])  # 纯代码接受，无分类

    def test_decline_offer_closes_declined(self, env):
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "算了，不用了")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed"
        assert pmr["close_reason"] == "declined"
        assert pmr["outcome"]["completed_parts"] == 0

    def test_ambiguous_proposing_reply_uses_classifier_affirmative_accepts(self, env):
        """模棱两可的接受（长句）→ 1 次 classifier；affirmative → 同轮 Beat1。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _classify_turn(env, sid, "嗯……听你这么一说好像挺舒服的，那就试试看吧", _k("affirmative"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active" and pmr["phase"] == "tension" and pmr["beats_done"] == 1
        assert "我们先从肩膀开始" in stream_prompt(env["provider"])
        assert "pmr_response" in agent_names(env["sink"])

    def test_ambiguous_proposing_negative_closes_without_starting(self, env):
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _classify_turn(env, sid, "我肩膀也不是很紧，就算了吧", _k("negative"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "declined"

    def test_closed_cooldown_no_reoffer(self, env):
        """closed 后冷却内不再自动重提（避免打扰）。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "算了，不用了")  # → closed
        cooldown_start = len(env["sink"].records)  # 快照：只统计 closed 之后的新轮
        # 冷却内又来身体紧绷消息 → gate 命中但不重提（closed_recent）
        env["provider"].script_stream("test-model", [["嗯"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "肩膀又紧起来了")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed"  # 仍是上轮的 closed，未再提议
        assert "pmr_opportunity" not in agent_names_since(env["sink"], cooldown_start)

    def test_offer_focus_hands_reorders_body_parts(self, env):
        """focus=hands：部位顺序重排，hands 提前并从 hands 开始。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid, msg="手好紧", offer=PMR_OFFER_HANDS)
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["current_part"] == "hands"
        assert pmr["body_parts"] == ["hands", "shoulders", "face"]

    def test_offer_focus_face_reorders_body_parts(self, env):
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid, msg="脸好僵", offer=PMR_OFFER_FACE)
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["current_part"] == "face"
        assert pmr["body_parts"] == ["face", "shoulders", "hands"]

    def test_offer_focus_general_uses_default_order_and_generic_line(self, env):
        """focus=general：默认顺序、通用邀请文案（无部位定语）。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid, msg="整个人都僵住了", offer=PMR_OFFER_NO_FOCUS)
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["current_part"] == "shoulders"
        assert pmr["body_parts"] == list(PMR_BODY_ORDER)
        prompt = stream_prompt(env["provider"])
        assert "身体还绷得很紧" in prompt
        assert "这边挺紧的" not in prompt

    def test_short_neg_refusal_closes_declined(self, env):
        """proposing 阶段短拒绝句（非 STOP_CUES，靠 NEG 前缀）→ declined。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "不用吧")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "declined"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 软确认 / 推进（不机械等「好了」）
# ═══════════════════════════════════════════════════════════════════════════════

class TestSoftConfirmationAdvance:
    def test_full_happy_path_to_completed(self, env):
        """accept → 6 拍（3 区域 × 2）→ 收尾轮 → 任意回复 completed。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "好啊")          # accept: 肩膀 Beat1, beats_done=1, tension
        pmr = _pmr_state(env["sessions"], sid)
        assert (pmr["beats_done"], pmr["parts_completed"], pmr["phase"]) == (1, 0, "tension")

        _code_turn(env, sid, "嗯")            # 软确认 → 肩膀 Beat2 (release), notice
        pmr = _pmr_state(env["sessions"], sid)
        assert "现在慢慢把肩膀放下来" in stream_prompt(env["provider"])
        assert (pmr["beats_done"], pmr["parts_completed"], pmr["phase"]) == (2, 1, "notice")

        _code_turn(env, sid, "松了点")        # → 双手 Beat1
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["current_part"] == "hands" and pmr["phase"] == "tension" and pmr["beats_done"] == 3
        assert "接下来是双手" in stream_prompt(env["provider"])

        _code_turn(env, sid, "舒服多了")       # → 双手 Beat2
        pmr = _pmr_state(env["sessions"], sid)
        assert (pmr["beats_done"], pmr["parts_completed"]) == (4, 2)

        _code_turn(env, sid, "可以")          # → 脸 Beat1
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["current_part"] == "face" and pmr["phase"] == "tension" and pmr["beats_done"] == 5

        _code_turn(env, sid, "还行")          # → 脸 Beat2
        pmr = _pmr_state(env["sessions"], sid)
        assert (pmr["beats_done"], pmr["parts_completed"]) == (6, 3)

        _code_turn(env, sid, "好")            # → 自然收尾轮（closing:true）
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["closing"] is True and pmr["status"] == "active"
        assert "今天先陪你到这里" in stream_prompt(env["provider"])

        _code_turn(env, sid, "嗯，松了一些")   # 收尾反馈 → completed
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed"
        assert pmr["close_reason"] == "completed"
        assert pmr["outcome"]["completed_parts"] == 3
        assert pmr["outcome"]["user_feedback"] == "嗯，松了一些"
        # 收尾块不出口：本轮是普通 daily（块空），宿主自然回应
        assert "身体放松支持" not in stream_prompt(env["provider"])

    def test_soft_confirm_never_blocks_or_calls_classifier(self, env):
        """软确认词回应 → 纯代码继续（推进阶段 0 classifier / 0 新机会调用）。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "好啊")
        start = len(env["sink"].records)  # 推进阶段从 accept 之后记账
        for msg in ("嗯", "好", "松了点", "舒服", "可以", "还行"):
            _code_turn(env, sid, msg)
        added = agent_names_since(env["sink"], start)
        assert added == {"daily", "safety", "settlement_profile", "portrait_eval"}  # 无 pmr LLM 调用
        assert "pmr_response" not in added and "pmr_opportunity" not in added

    def test_ambiguous_active_reply_classifier_continues(self, env):
        """模棱两可回复经 classifier 判 affirmative/neutral → 继续，不卡死。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "好啊")
        before = _pmr_state(env["sessions"], sid)["beats_done"]
        _classify_turn(env, sid, "也说不上，就是肩膀还有一点点紧", _k("neutral"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr["beats_done"] == before + 1  # Beat2 已推进

    def test_classifier_failure_defaults_to_continue(self, env):
        """classifier LLM 返回非法 JSON（fallback other）→ active 阶段默认继续，绝不卡死。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "好啊")
        before = _pmr_state(env["sessions"], sid)["beats_done"]
        # 返回非 JSON → aexecute fallback → kind=other → 引擎映射为继续
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", ["this is not json", PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "也说不上，就是有点奇怪")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active" and pmr["beats_done"] == before + 1

    def test_soft_continue_long_lead_falls_to_classifier(self, env):
        """软确认只在短分句成立；长分句回落 classifier，判 affirmative 仍继续。"""
        sid = env["sessions"].create("u1")
        _offer_pmr(env, sid)
        _code_turn(env, sid, "好啊")
        before = _pmr_state(env["sessions"], sid)["beats_done"]
        _classify_turn(env, sid, "松是松了，不过我现在说不上到底什么感觉", _k("affirmative"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr["beats_done"] == before + 1
        assert "pmr_response" in agent_names(env["sink"])


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 退出路径：discomfort / imaginal / stop / topic_shift（决策 6）
# ═══════════════════════════════════════════════════════════════════════════════

def _active_env(env, sid):
    _offer_pmr(env, sid)
    _code_turn(env, sid, "好啊")  # → active, 肩膀 Beat1, tension


class TestExitPaths:
    def test_mid_discomfort_closes(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _code_turn(env, sid, "肩膀有点酸，好像不太行")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "discomfort"
        assert pmr["outcome"]["completed_parts"] == 0

    def test_chest_or_emotional_pain_is_not_body_discomfort(self, env):
        """『心疼/心酸』含疼/酸但不带身体锚点 → 不当身体不适误关；classifier 决定。"""
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "其实我心里有点难受", _k("affirmative"))  # 仍在跟随 → 继续
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr.get("close_reason") is None  # 未误判身体不适

    def test_cannot_tense_switches_imaginal(self, env):
        """受伤/不能用力 → 改为想象式引导，不要求真的收紧。"""
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _code_turn(env, sid, "我肩膀疼，不能用力")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr["mode"] == "imaginal"
        assert pmr["phase"] == "tension"           # 仍处在收紧相，用想象重带这一拍
        assert pmr["beats_done"] == 1              # 不额外计拍
        assert "想象" in stream_prompt(env["provider"])

    def test_mid_stop_closes_stopped(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _code_turn(env, sid, "不做了，就这样吧")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "stopped"

    def test_topic_shift_full_leave_closes(self, env):
        """完全离开练习去讲别的话题 → topic_shift 关闭。"""
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _code_turn(env, sid, "先不说放松了，我想聊聊我妈妈的事")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "topic_shift"

    def test_decision6_new_topic_after_respond_continues(self, env):
        """🔴 决策 6：先回应本拍、再顺带提新话题 → 继续，不误关。"""
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        before = _pmr_state(env["sessions"], sid)["beats_done"]
        _code_turn(env, sid, "松了一点。对了，我明天还有考试，不知道能不能考好。")
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"                       # 没被关掉
        assert pmr["beats_done"] == before + 1                 # 已推进到下一拍
        assert "pmr_response" not in agent_names(env["sink"])  # 纯代码软确认先行

    def test_response_prompt_encodes_decision6_principle(self):
        from src.skills.prompts.pmr import PMR_RESPONSE_PROMPT as P
        assert "对了，我明天还有考试。" in P          # 规格里的例子逐字在 prompt 里
        assert "先回应了当前这一拍" in P
        assert "不要" in P and "topic_shift" in P

    def test_opportunity_prompt_boundaries(self):
        """机会 prompt 边界：不诊断、不教呼吸/冥想、心肺体感不提议。"""
        from src.skills.prompts.pmr import PMR_OPPORTUNITY_PROMPT as P
        assert "不该用肌肉放松处理" in P
        assert "不是诊断" in P
        assert "不做呼吸训练、不教冥想/正念" in P or "不教冥想" in P

    def test_classifier_cannot_tense_switches_imaginal(self, env):
        """代码层无不适 cue、classifier 判 cannot_tense → 切想象式，不额外计拍。"""
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "我这边不太方便做收紧，但挺想继续的", _k("cannot_tense"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "active"
        assert pmr["mode"] == "imaginal"
        assert pmr["phase"] == "tension"
        assert pmr["beats_done"] == 1
        assert "想象" in stream_prompt(env["provider"])

    def test_classifier_discomfort_closes(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "我也说不上，就是哪里不对劲", _k("discomfort"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "discomfort"

    def test_classifier_topic_shift_closes(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "对了，我最近刚换了工作", _k("topic_shift"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "topic_shift"

    def test_classifier_negative_stops(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "说不上来，好像也没什么用", _k("negative"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "stopped"

    def test_classifier_pause_stops(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        _classify_turn(env, sid, "让我先缓一缓再说", _k("pause"))
        pmr = _pmr_state(env["sessions"], sid)
        assert pmr["status"] == "closed" and pmr["close_reason"] == "stopped"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. 优先级 / 互斥 / 清理
# ═══════════════════════════════════════════════════════════════════════════════

AFFECT_OFFER = json.dumps({
    "should_offer": True, "candidates": ["委屈", "失望", "焦虑"], "reason": "模糊",
}, ensure_ascii=False)


class TestPriorityMutualExclusion:
    def test_pmr_beats_affect_same_turn(self, env_both):
        """同一句既像身体紧绷又像情绪模糊 → PMR 优先：affect 本轮跳过。"""
        sid = env_both["sessions"].create("u1")
        env_both["provider"].script_stream("test-model", [["嗯"]])
        env_both["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_both["provider"].script_complete("test-model", [PMR_OFFER, PROFILE_NOCHANGE])
        run_turn(env_both["scheduler"], "u1", sid, "我说不上怎么了，肩膀紧绷绷的")
        state = env_both["sessions"].read_state(sid)
        assert state["pmr"]["status"] == "proposing"
        assert state.get("affect_labeling") is None
        agents = agent_names(env_both["sink"])
        assert "pmr_opportunity" in agents
        assert "affect_labeling_opportunity" not in agents  # affect 本轮被 PMR 抢占
        prompt = stream_prompt(env_both["provider"])
        assert "身体放松支持" in prompt and "## 情绪命名协助" not in prompt

    def test_pmr_supersedes_live_affect_proposing(self, env_both):
        """PMR 抢占尚未结束的 affect proposing（沿用 affect switched 清残留语义）。"""
        sid = env_both["sessions"].create("u1")
        # turn1：纯情绪 → affect 提出候选（pmr 空闲不掺和）
        env_both["provider"].script_stream("test-model", [["嗯"]])
        env_both["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_both["provider"].script_complete("test-model", [AFFECT_OFFER, PROFILE_NOCHANGE])
        run_turn(env_both["scheduler"], "u1", sid, "我真的特别难受，说不上怎么了")
        assert env_both["sessions"].read_state(sid)["affect_labeling"]["status"] == "proposing"
        # turn2：身体紧绷请求 → PMR offer，残留 affect proposing 被清
        env_both["provider"].script_stream("test-model", [["嗯"]])
        env_both["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_both["provider"].script_complete("test-model", [PMR_OFFER, PROFILE_NOCHANGE])
        run_turn(env_both["scheduler"], "u1", sid, "肩膀好紧，先别管那个情绪了，帮我把身体松一松")
        state = env_both["sessions"].read_state(sid)
        assert state["pmr"]["status"] == "proposing"
        assert state.get("affect_labeling") is None  # 被 supersede

    def test_affect_runs_when_pmr_idle(self, env_both):
        """纯情绪消息（pmr gate 不命中）→ affect 照常提议，pmr 不掺和。"""
        sid = env_both["sessions"].create("u1")
        env_both["provider"].script_stream("test-model", [["嗯"]])
        env_both["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env_both["provider"].script_complete("test-model", [AFFECT_OFFER, PROFILE_NOCHANGE])
        run_turn(env_both["scheduler"], "u1", sid, "我很烦，说不上为什么")
        state = env_both["sessions"].read_state(sid)
        assert state["affect_labeling"]["status"] == "proposing"
        assert state.get("pmr") is None
        agents = agent_names(env_both["sink"])
        assert "affect_labeling_opportunity" in agents
        assert "pmr_opportunity" not in agents

    def test_crisis_yields_to_safety_and_clears_pmr(self, env):
        sid = env["sessions"].create("u1")
        _active_env(env, sid)
        crisis_start = len(env["sink"].records)
        env["provider"].script_stream("test-model", [["我陪着你"]])
        env["provider"].script_complete("test-cheap", [SAFETY_CRISIS])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "我不想活了")
        state = env["sessions"].read_state(sid)
        assert state["crisis"] is True
        assert state.get("pmr") is None          # CrisisDetected 信号清空
        added = agent_names_since(env["sink"], crisis_start)
        assert "pmr_opportunity" not in added and "pmr_response" not in added

    def test_switch_therapy_and_crisis_signals_clear_pmr(self, tmp_path):
        sessions = SessionStore(data_root=str(tmp_path))
        sid = sessions.create("u1")
        st = SessionState.from_dict(sessions.read_state(sid))
        st.pmr = {"status": "active", "phase": "tension", "beats_done": 1}
        SwitchToTherapy(therapy="CBT").apply(st)
        assert st.pmr is None
        st.pmr = {"status": "proposing"}
        CrisisDetected().apply(st)
        assert st.pmr is None


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Engine 零副作用 + 状态机单元回归（不经 Scheduler）
# ═══════════════════════════════════════════════════════════════════════════════

class TestEngineDiscipline:
    def test_step_zero_side_effect_off_offer(self, tmp_path, monkeypatch):
        """off + gate 命中 → step 产出 offer，但 ctx.state 快照分毫未动。"""
        _monkey_env(monkeypatch, tmp_path)
        client, provider, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine(client, session_store=sessions)
        provider.script_complete("test-model", [PMR_OFFER])
        turn, updates = _eng_turn(eng, sessions, "s1", "肩膀好紧")
        assert turn is not None and turn["kind"] == "offer"
        assert updates["pmr"]["status"] == "proposing"
        assert updates["pmr"]["current_part"] == "shoulders"

    def test_step_zero_side_effect_active(self, tmp_path):
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)
        eng.sessions = sessions  # __new__ 绕过 __init__，补齐冷却判定要用的会话簿
        # 构造无 llm 的引擎也能测纯代码路径：step 不触 LLM（软确认），零副作用照旧
        turn, updates = _eng_turn(eng, sessions, "s1", "嗯",
                                  pmr_seed=_active_seed(beats_done=1, phase="tension"))
        assert turn is not None and turn["kind"] == "beat" and turn["beat_no"] == 2
        assert updates["pmr"]["phase"] == "notice"
        assert updates["pmr"]["beats_done"] == 2

    def test_step_off_gate_miss_returns_none_untouched(self, tmp_path):
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)
        eng.sessions = sessions  # __new__ 绕过 __init__，补齐会话簿
        turn, updates = _eng_turn(eng, sessions, "s1", "今天天气不错")
        assert turn is None and updates == {}

    def test_cooldown_suppresses_offer(self, tmp_path):
        """closed 不久 → 即便 gate 命中也不再重提（冷却内不打扰）。"""
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)
        eng.sessions = sessions  # __new__ 绕过 __init__，补齐冷却判定要用的会话簿
        seed = {"status": "closed", "closed_seq": 1, "start_seq": 1, "close_reason": "declined",
                "outcome": {}, "body_parts": list(PMR_BODY_ORDER), "mode": "gentle"}
        turn, updates = _eng_turn(eng, sessions, "s1", "肩膀又紧了", pmr_seed=seed)
        assert turn is None and updates == {}

    def test_run_switch_therapy_clears_pmr(self, tmp_path):
        """engine.run(switched_to_therapy=True) 在 pmr 非空时清残留。"""
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)
        eng.sessions = sessions
        ctx, _ = _engine_ctx(sessions, "s1", "嗯", pmr_seed=_active_seed())
        updates = asyncio.run(eng.run(ctx, switched_to_therapy=True))
        assert updates == {"pmr": None}

    def test_run_switch_therapy_no_pmr_noop(self, tmp_path):
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)
        eng.sessions = sessions
        ctx, _ = _engine_ctx(sessions, "s1", "嗯", pmr_seed=None)
        updates = asyncio.run(eng.run(ctx, switched_to_therapy=True))
        assert updates == {}

    def test_opportunity_llm_exception_returns_none(self, tmp_path, monkeypatch):
        """机会 LLM 抛异常 → step 退回 None（零副作用），不阻塞主流程。"""
        _monkey_env(monkeypatch, tmp_path)
        client, _, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine(client, session_store=sessions)

        class _Boom:
            async def aexecute(self, inputs, ctx):
                raise RuntimeError("boom")

        eng._opportunity_skill = lambda: _Boom()

        async def go():
            ctx, st = _engine_ctx(sessions, "s1", "肩膀好紧")
            before = st.to_dict()
            turn = await eng.step(ctx)
            assert st.to_dict() == before
            return turn
        assert asyncio.run(go()) is None

    def test_opportunity_skill_missing_returns_none(self, tmp_path, monkeypatch):
        _monkey_env(monkeypatch, tmp_path)
        client, _, _ = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        eng = ProgressiveMuscleRelaxationEngine(client, session_store=sessions)
        eng._opportunity_skill = lambda: None

        async def go():
            ctx, _ = _engine_ctx(sessions, "s1", "肩膀好紧")
            return await eng.step(ctx)
        assert asyncio.run(go()) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Lexicon 完整性
# ═══════════════════════════════════════════════════════════════════════════════

class TestLexiconIntegrity:
    def test_no_empty_or_whitespace_cues(self):
        groups = [lexicon.CHEST_CARDIAC_CUES, lexicon.BODY_TENSION_CUES,
                  lexicon.RELAXATION_REQUEST_CUES, lexicon.NEW_TOPIC_LEAVE_CUES,
                  lexicon.DISCOMFORT_CUES, lexicon.IMAGINAL_CUES, lexicon.STOP_CUES,
                  lexicon.ACCEPT_CUES, lexicon.SHORT_SOFT_CONTINUE_CUES,
                  lexicon.BODY_REGION_ANCHORS]
        for g in groups:
            assert all(isinstance(c, str) and c.strip() == c and c for c in g), f"{g} 有空/含空白词"
            assert len(g) == len(set(g)), f"{g} 有重复"

    def test_body_tension_disjoint_from_chest_cardiac(self):
        """紧绷线索绝不含胸/心慌体感（PMR 不碰心肺区）。"""
        assert set(lexicon.BODY_TENSION_CUES).isdisjoint(lexicon.CHEST_CARDIAC_CUES)
        assert set(lexicon.BODY_TENSION_MARKERS).isdisjoint(lexicon.CHEST_CARDIAC_CUES)
        # 胸闷/心慌等不进紧绷、也不进放松请求线索
        assert set(lexicon.RELAXATION_REQUEST_CUES).isdisjoint(lexicon.CHEST_CARDIAC_CUES)

    def test_tension_markers_never_include_chest_core_chars(self):
        """组合标记不含 心/胸（情绪化「心里紧」与心肺「胸口紧」结构性隔离）。"""
        for m in lexicon.BODY_TENSION_MARKERS:
            assert m not in ("心", "胸", "肺")

    def test_crisis_reexports_affect_source(self):
        from src.skills.affect_lexicon import CRISIS_CUES as AFFECT_CRISIS
        assert tuple(lexicon.CRISIS_CUES) == tuple(AFFECT_CRISIS)

    def test_chest_cues_never_anchor_body_tension(self):
        """确定性的 accept/continue 不应被心肺体感词触碰。"""
        for cue in lexicon.CHEST_CARDIAC_CUES:
            assert cue not in lexicon.BODY_TENSION_CUES
            assert cue not in lexicon.RELAXATION_REQUEST_CUES


# ═══════════════════════════════════════════════════════════════════════════════
# 9. pmr_engine=None：运行时零 PMR 影响
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoneEngineZeroChange:
    def test_no_engine_no_pmr_behavior(self, tmp_path, monkeypatch):
        """DecisionLine 不装 pmr_engine → 零 PMR 影响：无 PMR 提示/agent/状态推进。"""
        _monkey_env(monkeypatch, tmp_path)
        client, provider, sink = make_fake_client()
        sessions = SessionStore(data_root=str(tmp_path))
        profiles = ProfileStore(data_root=str(tmp_path))
        scheduler = Scheduler(
            dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions),
                                       session_store=sessions),
            decision_line=DecisionLine(SafetyAgent(client), session_store=sessions),
            settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
            session_store=sessions,
            profile_store=profiles,
        )
        sid = sessions.create("u1")
        provider.script_stream("test-model", [["好"]])
        provider.script_complete("test-cheap", [SAFETY_SAFE])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(scheduler, "u1", sid, "肩膀好紧，帮我放松一下")
        state = sessions.read_state(sid)
        assert state.get("pmr") is None
        assert state["owner"] == "daily"
        # 唯一差别：持久化面多一个固定 pmr:null 键
        assert "pmr" in state
        agents = agent_names(sink)
        assert {"daily", "safety", "settlement_profile", "portrait_eval"} == agents
        assert "pmr_opportunity" not in agents and "pmr_response" not in agents
        prompt = stream_prompt(provider)
        assert "身体放松支持" not in prompt and "要不要" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 10. 提议/接受/拒绝的精确边界（_short_refusal / _accept_short，纯代码）
# ═══════════════════════════════════════════════════════════════════════════════

class TestShortRefusalAcceptBoundary:
    def _eng(self):
        return ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)

    def test_short_refusal_neg_prefix(self):
        assert self._eng()._short_refusal("不用吧") is True

    def test_short_refusal_haishi_prefix(self):
        assert self._eng()._short_refusal("还是不了") is True

    def test_short_refusal_long_neg_not_refusal(self):
        assert self._eng()._short_refusal("不要这样吧我觉得还是想再试试") is False

    def test_accept_short_accepts(self):
        assert self._eng()._accept_short("嗯嗯好的") is True

    def test_accept_short_rejects_neg_prefix(self):
        """防御性分支：NEG 前缀开头的短句即便含接受词也不判接受。"""
        assert self._eng()._accept_short("还是好想试试") is False

    def test_accept_short_rejects_long(self):
        assert self._eng()._accept_short("好呀好呀那我们赶紧开始吧") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 11. 不适判定排除项（_PLEASANT_EXCLUSIONS）与身体锚点（纯代码）
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnchoredPain:
    def _eng(self):
        return ProgressiveMuscleRelaxationEngine.__new__(ProgressiveMuscleRelaxationEngine)

    def test_pleasant_exclusion_not_discomfort(self):
        eng = self._eng()
        assert eng._anchored_pain("挺痛快的") is False       # 「痛快」是舒服不是疼
        assert eng._anchored_pain("这动作有点麻烦") is False  # 「麻烦」不是麻

    def test_requires_body_anchor(self):
        eng = self._eng()
        assert eng._anchored_pain("肩膀有点酸") is True        # 对照：不适 cue + 身体锚点
        assert eng._anchored_pain("心疼") is False             # 无身体锚点 → 不当身体不适


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Host 渲染块 _pmr_block 的 kind 过滤（纯函数，保 golden 字节面）
# ═══════════════════════════════════════════════════════════════════════════════

class TestHostPmrBlock:
    def test_none_returns_empty(self):
        assert HostDialogueAgent._pmr_block(None) == ""

    def test_ack_close_returns_empty(self):
        assert HostDialogueAgent._pmr_block({"kind": "ack_close", "line": "x"}) == ""

    def test_closing_feedback_returns_empty(self):
        assert HostDialogueAgent._pmr_block({"kind": "closing_feedback", "line": None}) == ""

    def test_beat_returns_block_with_line(self):
        out = HostDialogueAgent._pmr_block({"kind": "beat", "line": "慢慢放松"})
        assert "身体放松支持" in out and "慢慢放松" in out

    def test_empty_line_returns_empty(self):
        assert HostDialogueAgent._pmr_block({"kind": "offer", "line": ""}) == ""


# ═══════════════════════════════════════════════════════════════════════════════
# 13. _lead_sentence 断句（纯代码）
# ═══════════════════════════════════════════════════════════════════════════════

class TestLeadSentence:
    def test_splits_on_period(self):
        assert _lead_sentence("松了一点。对了，考试") == "松了一点"

    def test_splits_on_exclaim(self):
        assert _lead_sentence("嗯！好的") == "嗯"

    def test_no_delimiter_returns_whole(self):
        assert _lead_sentence("松了但还有点紧") == "松了但还有点紧"

    def test_empty_returns_empty(self):
        assert _lead_sentence("") == ""
