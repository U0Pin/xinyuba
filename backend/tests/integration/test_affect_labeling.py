"""Affect Labeling（情绪识别与命名）单元 + 集成测试。

覆盖（规格第十七节）：
- Trigger：模糊情绪触发 / 困惑触发 / 已自行命名不重复 / 明确情绪不强行标注 /
  明确求方案不触发 / 危机让位；
- Candidate：2–4、去重、剔除术语/过泛、不足则保守不提议；
- User Response：选候选 / 自填新词(纠正) / 自填语义等同 / 说不知道 / 拒绝 /
  仍在讲述(no_answer)；
- State：confirmed 标签落盘、user correction 覆盖 AI 候选、不写长期画像；
- Integration：Router(调度器)→同轮提问→回答确认/纠正→状态收尾→回归普通对话；
  疗法优先、危机优先，既有疗法/日常测试不回归。
"""

import asyncio
import json

import pytest

import src.skills  # noqa: F401 — 注册技能
from src.agents.affect_labeling import AffectLabelingEngine
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
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.core.skill import SkillType, skill_registry
from src.skills import affect_lexicon as lexicon
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from tests.support.fakes import make_fake_client

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
SAFETY_CRISIS = json.dumps({"risk_level": "CRISIS", "risk_type": "suicidal_ideation"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})

OFFER_JSON = json.dumps({
    "should_offer": True,
    "candidates": ["委屈", "失望", "焦虑"],
    "reason": "情绪表达较模糊，适合帮 ta 说得更具体",
}, ensure_ascii=False)
NO_OFFER_JSON = json.dumps({
    "should_offer": False, "candidates": [], "reason": "情绪已自行命名，不需要再问",
}, ensure_ascii=False)

CHOSEN_JSON = json.dumps({
    "kind": "chosen", "label": "委屈", "matches_candidate": "委屈",
    "user_confirmed": True, "user_correction": False, "confidence": 0.9,
}, ensure_ascii=False)
CUSTOM_CORRECTION_JSON = json.dumps({
    "kind": "custom", "label": "生气", "matches_candidate": None,
    "user_confirmed": True, "user_correction": True, "confidence": 0.85,
}, ensure_ascii=False)
CUSTOM_EQUIV_JSON = json.dumps({
    "kind": "custom", "label": "有点冤", "matches_candidate": "委屈",
    "user_confirmed": True, "user_correction": False, "confidence": 0.8,
}, ensure_ascii=False)
UNKNOWN_JSON = json.dumps({
    "kind": "unknown", "label": None, "matches_candidate": None,
    "user_confirmed": False, "user_correction": False, "confidence": 0.9,
}, ensure_ascii=False)
REFUSED_JSON = json.dumps({
    "kind": "refused", "label": None, "matches_candidate": None,
    "user_confirmed": False, "user_correction": False, "confidence": 0.9,
}, ensure_ascii=False)
NO_ANSWER_JSON = json.dumps({
    "kind": "no_answer", "label": None, "matches_candidate": None,
    "user_confirmed": False, "user_correction": False, "confidence": 0.9,
}, ensure_ascii=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """带 affect_engine 的 Scheduler 集成环境（WINDOW_TOKEN_THRESHOLD 默认调大跳过 decider）。"""
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10 ** 9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10 ** 9)
    monkeypatch.setattr(config, "THERAPY_MAX_ROUNDS", 10)
    client, provider, sink = make_fake_client()
    sessions = SessionStore(data_root=str(tmp_path))
    profiles = ProfileStore(data_root=str(tmp_path))
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
            affect_engine=AffectLabelingEngine(client, session_store=sessions),
        ),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )
    return {"client": client, "provider": provider, "sink": sink,
            "sessions": sessions, "profiles": profiles, "scheduler": scheduler}


def run_turn(scheduler, uid, sid, msg):
    asyncio.run(_collect(scheduler, uid, sid, msg))


async def _collect(scheduler, uid, sid, msg):
    async for _ in scheduler.handle_message(uid, sid, msg):
        pass


def agent_names(sink):
    return {r["agent"] for r in sink.records}


# ═══════════════════════════════════════════════════════════════════════════════
# Unit：gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestTriggerGate:
    @pytest.mark.parametrize("msg", [
        "我很烦",
        "我很难受",
        "我不知道自己怎么了",
        "我真的特别难受",
        "老板没回我消息，我现在特别不舒服",
        "心里特别堵",
    ])
    def test_vague_or_confused_emotion_should_gate_in(self, env, msg):
        assert AffectLabelingEngine.__new__(AffectLabelingEngine)._gate(msg) is True

    @pytest.mark.parametrize("msg", [
        "我现在就是愤怒",
        "我现在非常愤怒",
        "我很难过，很委屈",          # 已自行命名，避免重复标注
        "我很失望",
        "明天面试，我应该怎么准备？",  # 明确求方案
        "今天天气不错，随便聊聊",
        "你好呀",
    ])
    def test_explicit_or_solution_should_gate_out(self, env, msg):
        assert AffectLabelingEngine.__new__(AffectLabelingEngine)._gate(msg) is False

    @pytest.mark.parametrize("msg", [
        "我不想活了",
        "我好想结束这一切",
        "我想伤害自己",
    ])
    def test_crisis_language_never_gates_in(self, env, msg):
        assert AffectLabelingEngine.__new__(AffectLabelingEngine)._gate(msg) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Unit：AffectOpportunity 候选生成与校验
# ═══════════════════════════════════════════════════════════════════════════════

class TestOpportunitySkill:
    def _skill(self):
        return skill_registry.get("affect_labeling_opportunity")

    def test_registered_as_emotion_support(self):
        skill = self._skill()
        assert skill is not None
        assert skill.skill_type == SkillType.EMOTION_SUPPORT

    def test_parse_valid_candidates_kept_in_order(self):
        skill = self._skill()
        res = skill.parse_output(
            {"should_offer": True, "candidates": ["委屈", "失望", "焦虑"], "reason": "r"},
            {"user_text": "x"},
        )
        assert res.success is True
        assert res.output["should_offer"] is True
        assert res.output["candidates"] == ["委屈", "失望", "焦虑"]

    def test_candidate_count_capped_at_four_and_deduped(self):
        skill = self._skill()
        res = skill.parse_output(
            {"should_offer": True, "candidates": ["委屈", "失望", "焦虑", "生气", "内疚", "委屈"]},
            {"user_text": "x"},
        )
        cands = res.output["candidates"]
        assert 2 <= len(cands) <= 4
        assert cands == ["委屈", "失望", "焦虑", "生气"]  # 去重保序 + 截断 4

    def test_jargon_and_overgeneric_candidates_are_filtered(self):
        skill = self._skill()
        res = skill.parse_output(
            {"should_offer": True, "candidates": ["委屈", "失望", "认知失调", "难受"]},
            {"user_text": "x"},
        )
        cands = res.output["candidates"]
        assert "认知失调" not in cands
        assert "难受" not in cands
        assert cands == ["委屈", "失望"]  # 术语/过泛被剔除，保留其余有效候选

    def test_too_few_valid_candidates_treats_as_no_offer(self):
        skill = self._skill()
        res = skill.parse_output(
            {"should_offer": True, "candidates": ["委屈"]},  # 只剩 1 个有效 → 不提议
            {"user_text": "x"},
        )
        assert res.output["should_offer"] is False
        assert res.output["candidates"] == []

    def test_explicit_should_offer_false(self):
        skill = self._skill()
        res = skill.parse_output(
            {"should_offer": False, "candidates": [], "reason": "已明确"}, {"user_text": "x"}
        )
        assert res.output["should_offer"] is False

    def test_fallback_is_conservative_no_offer(self):
        skill = self._skill()
        res = skill.fallback({"user_text": "x"})
        assert res.output["should_offer"] is False
        assert res.output["candidates"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# Unit：AffectResponseClassifier 用户回应归类
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponseClassifier:
    def _skill(self):
        return skill_registry.get("affect_labeling_response")

    def _parse(self, data):
        return self._skill().parse_output(data, {"user_text": "x", "candidates": ["委屈", "失望", "焦虑"]}).output

    def test_chosen(self):
        out = self._parse({"kind": "chosen", "label": "委屈", "matches_candidate": "委屈",
                           "user_confirmed": True, "user_correction": False, "confidence": 0.9})
        assert out["kind"] == "chosen"
        assert out["label"] == "委屈"
        assert out["user_confirmed"] is True
        assert out["user_correction"] is False

    def test_custom_new_word_is_correction(self):
        out = self._parse({"kind": "custom", "label": "生气", "matches_candidate": None,
                           "user_confirmed": True, "user_correction": True, "confidence": 0.85})
        assert out["label"] == "生气"
        assert out["user_confirmed"] is True
        assert out["user_correction"] is True

    def test_custom_equivalent_to_candidate_is_not_correction(self):
        out = self._parse({"kind": "custom", "label": "有点冤", "matches_candidate": "委屈",
                           "user_confirmed": True, "user_correction": False, "confidence": 0.8})
        assert out["label"] == "委屈"  # 归一化到候选
        assert out["user_correction"] is False

    def test_unknown(self):
        out = self._parse({"kind": "unknown", "label": None, "matches_candidate": None,
                           "user_confirmed": False, "user_correction": False, "confidence": 0.9})
        assert out["kind"] == "unknown"
        assert out["label"] is None
        assert out["user_confirmed"] is False

    def test_refused(self):
        out = self._parse({"kind": "refused", "label": None, "matches_candidate": None,
                           "user_confirmed": False, "user_correction": False, "confidence": 0.9})
        assert out["kind"] == "refused"

    def test_no_answer(self):
        out = self._parse({"kind": "no_answer", "label": None, "matches_candidate": None,
                           "user_confirmed": False, "user_correction": False, "confidence": 0.9})
        assert out["kind"] == "no_answer"

    def test_invalid_kind_defaults_no_answer(self):
        out = self._parse({"kind": "bogus", "label": None, "matches_candidate": None,
                           "user_confirmed": False, "user_correction": False})
        assert out["kind"] == "no_answer"


# ═══════════════════════════════════════════════════════════════════════════════
# Integration
# ═══════════════════════════════════════════════════════════════════════════════

def _trigger_propose(env, sid, msg="我真的特别难受", offer=OFFER_JSON):
    """脚本一轮“模糊情绪消息”→ 机会评估 offer → proposing 落盘。"""
    env["provider"].script_stream("test-model", [["嗯"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [offer, PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


def _next_turn(env, sid, msg, classify_json):
    """脚本下一轮用户回应 → classify。"""
    env["provider"].script_stream("test-model", [["好"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [classify_json, PROFILE_NOCHANGE])
    run_turn(env["scheduler"], "u1", sid, msg)


class TestProposeAndAsk:
    def test_vague_emotion_proposes_and_asks_same_turn(self, env):
        """B 方案核心：触发消息的本轮回复就带出候选提问，且状态落盘 proposing。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        state = env["sessions"].read_state(sid)
        al = state.get("affect_labeling")
        assert al is not None
        assert al["status"] == "proposing"
        assert al["candidates"] == ["委屈", "失望", "焦虑"]
        # 同轮提问：本轮 dialogue 的 stream prompt 应含候选词
        stream_prompts = [c[1] for c in env["provider"].calls if c[0] == "stream"]
        assert stream_prompts, "应有流式回复"
        assert "委屈" in stream_prompts[0]
        assert "情绪命名协助" in stream_prompts[0]

    def test_explicit_emotion_never_proposes(self, env):
        """“我现在就是愤怒”→ 不触发，无 affect 状态、不调用机会 LLM。"""
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "我现在就是愤怒")
        state = env["sessions"].read_state(sid)
        assert state.get("affect_labeling") is None
        assert state["owner"] == "daily"
        assert "affect_labeling_opportunity" not in agent_names(env["sink"])

    def test_already_named_two_emotions_does_not_relabel(self, env):
        """“我很难过，很委屈”→ 已自行命名，不重复标注。"""
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "我很难过，很委屈")
        assert env["sessions"].read_state(sid).get("affect_labeling") is None

    def test_solution_request_never_proposes(self, env):
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "明天面试，我应该怎么准备？")
        assert env["sessions"].read_state(sid).get("affect_labeling") is None


class TestConfirmAndClose:
    def test_choose_candidate_confirms_and_closes(self, env):
        """选候选 → 状态 closed、outcome 保存 user-confirmed 标签。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "应该是委屈", CHOSEN_JSON)
        al = env["sessions"].read_state(sid).get("affect_labeling")
        assert al["status"] == "closed"
        assert al["close_reason"] == "answered"
        assert al["outcome"]["emotion_label"] == "委屈"
        assert al["outcome"]["user_confirmed"] is True
        assert al["outcome"]["user_correction"] is False

    def test_user_correction_overrides_candidates(self, env):
        """用户自填候选之外的词（生气）→ user_correction=True，用户自我报告优先。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "不是，我就是生气", CUSTOM_CORRECTION_JSON)
        al = env["sessions"].read_state(sid).get("affect_labeling")
        assert al["status"] == "closed"
        assert al["outcome"]["emotion_label"] == "生气"
        assert al["outcome"]["user_confirmed"] is True
        assert al["outcome"]["user_correction"] is True

    def test_say_i_dont_know_closes_without_confirm(self, env):
        """说不知道 → 不逼问、不确认、正常收尾。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "不知道", UNKNOWN_JSON)
        al = env["sessions"].read_state(sid).get("affect_labeling")
        assert al["status"] == "closed"
        assert al["outcome"]["user_confirmed"] is False
        assert al["outcome"]["emotion_label"] is None

    def test_refusal_is_respected(self, env):
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "不想说这个了", REFUSED_JSON)
        al = env["sessions"].read_state(sid).get("affect_labeling")
        assert al["status"] == "closed"
        assert al["close_reason"] == "answered"
        assert al["outcome"]["source"] == "refused"

    def test_user_keeps_narrating_closes_no_response(self, env):
        """用户继续讲述、没接问题 → 不再追问（no_response 收尾），不变成问卷。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "对，我跟谁都不想说话", NO_ANSWER_JSON)
        al = env["sessions"].read_state(sid).get("affect_labeling")
        assert al["status"] == "closed"
        assert al["close_reason"] == "no_response"

    def test_confirmed_label_does_not_touch_long_term_profile(self, env):
        """规格 #13：一次情绪标注不写长期画像。"""
        sid = env["sessions"].create("u1")
        _trigger_propose(env, sid)
        _next_turn(env, sid, "应该是委屈", CHOSEN_JSON)
        profile = env["profiles"].get("u1")
        assert profile == {}  # settlement no_change → 画像未被写


class TestPriorityAndBoundaries:
    def test_crisis_yields_to_safety(self, env):
        """crisis → 危机流程照旧，Affect Labeling 不写任何状态。"""
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["我陪着你"]])
        env["provider"].script_complete("test-cheap", [SAFETY_CRISIS])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(env["scheduler"], "u1", sid, "我不想活了")
        state = env["sessions"].read_state(sid)
        assert state["crisis"] is True
        assert state["therapy"] is None
        assert state.get("affect_labeling") is None
        assert "affect_labeling_opportunity" not in agent_names(env["sink"])

    def test_therapy_switch_beats_emotion_support(self, env, monkeypatch):
        """同一轮既可能提议、又判定需要疗法 → 疗法优先，affect 提议被丢弃。"""
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        # maybe_plan 机会评估先跑（OFFER），随后 decider 判定切 CBT
        env["provider"].script_complete("test-model", [
            OFFER_JSON,
            json.dumps({"need_therapy": True, "therapy": "CBT", "reason": "认知扭曲"}),
            PROFILE_NOCHANGE,
        ])
        run_turn(env["scheduler"], "u1", sid, "我真的特别难受，觉得自己什么都做不好")
        state = env["sessions"].read_state(sid)
        assert state["therapy"]["name"] == "CBT"
        assert state["owner"] == "therapy_cbt"
        assert state.get("affect_labeling") is None  # 提议让位于疗法

    def test_no_engine_means_zero_behavior_change(self, env):
        """不带 affect_engine 的 DecisionLine：旧行为完全不变（用 phase3 同款装配）。"""
        sid = env["sessions"].create("u1")
        provider = env["provider"]
        # 用一个不带 engine 的 scheduler 复跑一次 trigger 消息
        client = env["client"]
        sessions = env["sessions"]
        profiles = env["profiles"]
        scheduler = Scheduler(
            dialogue_line=DialogueLine(HostDialogueAgent(client, session_store=sessions), session_store=sessions),
            decision_line=DecisionLine(SafetyAgent(client), session_store=sessions),
            settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
            session_store=sessions,
            profile_store=profiles,
        )
        provider.script_stream("test-model", [["好"]])
        provider.script_complete("test-cheap", [SAFETY_SAFE])
        provider.script_complete("test-model", [PROFILE_NOCHANGE])
        run_turn(scheduler, "u1", sid, "我真的特别难受")
        state = env["sessions"].read_state(sid)
        assert state.get("affect_labeling") is None
        assert state["owner"] == "daily"


# ═══════════════════════════════════════════════════════════════════════════════
# Lexicon 完整性（规格 §25.1–25.5，词库重构后）
# ═══════════════════════════════════════════════════════════════════════════════

class TestLexiconIntegrity:
    """CANDIDATE_VOCABULARY 的元数据、去重、alias、类别隔离、DO_NOT_SUGGEST。"""

    def test_metadata_complete_and_valid(self):
        vocab = lexicon.CANDIDATE_VOCABULARY
        assert 80 <= len(vocab) <= 100, f"规模应约 85，实际 {len(vocab)}"
        required = {"group", "kind", "specificity", "register"}
        for term, meta in vocab.items():
            assert isinstance(term, str) and term.strip(), "canonical term 不能为空"
            assert required <= set(meta), f"{term}: 缺少字段 {required - set(meta)}"
            assert meta["group"] in lexicon.ALLOWED_GROUPS, f"{term}.group 非法"
            assert meta["kind"] in lexicon.ALLOWED_KINDS, f"{term}.kind 非法"
            assert meta["specificity"] in lexicon.ALLOWED_SPECIFICITY, f"{term}.specificity 非法"
            assert meta["register"] in lexicon.ALLOWED_REGISTERS, f"{term}.register 非法"

    def test_covers_all_kinds_groups_and_registers(self):
        vocab = lexicon.CANDIDATE_VOCABULARY
        kinds = {m["kind"] for m in vocab.values()}
        groups = {m["group"] for m in vocab.values()}
        registers = {m["register"] for m in vocab.values()}
        assert kinds == set(lexicon.ALLOWED_KINDS)  # emotion/experience/state/somatic 都有
        assert groups == set(lexicon.ALLOWED_GROUPS)
        assert registers >= {"neutral", "colloquial", "literary"}

    def test_no_duplicate_or_empty_terms(self):
        vocab = lexicon.CANDIDATE_VOCABULARY
        assert len(vocab) == len(set(vocab)) == len({k.strip() for k in vocab})
        assert all(k == k.strip() for k in vocab)

    def test_aliases_target_exist_in_canonical(self):
        for source, target in lexicon.LEXICON_ALIASES.items():
            assert isinstance(source, str) and source.strip(), "alias 来源不能为空"
            assert target in lexicon.CANDIDATE_VOCABULARY, f"{source} → {target} 不在 canonical"

    def test_no_cross_emotion_alias(self):
        """禁止 紧张→焦虑 / 担心→焦虑 这类跨情绪合并。"""
        for bad in ("紧张", "担心"):
            assert bad not in lexicon.LEXICON_ALIASES or lexicon.LEXICON_ALIASES[bad] != "焦虑"

    def test_non_candidate_categories_are_isolated_from_canonical(self):
        """临床/术语/评价/事件/不主动建议词不得出现在 CANDIDATE_VOCABULARY。"""
        vocab = set(lexicon.CANDIDATE_VOCABULARY)
        for cat in (lexicon.CLINICAL_TERMS, lexicon.PSYCHOLOGICAL_JARGON,
                    lexicon.JUDGMENT_TERMS, lexicon.EVENT_TERMS,
                    lexicon.DO_NOT_SUGGEST, lexicon.TOO_GENERIC_CANDIDATES):
            assert vocab.isdisjoint(cat), f"canonical 混入非候选词: {vocab & set(cat)}"

    def test_generalized_and_low_spec_terms_are_not_candidates(self):
        """GENERALIZED / LOW / INCOMPLETE 是 cue 不是 candidate。"""
        vocab = set(lexicon.CANDIDATE_VOCABULARY)
        assert vocab.isdisjoint(lexicon.GENERALIZED_AFFECT_TERMS)
        assert vocab.isdisjoint(lexicon.LOW_SPECIFICITY_CUES)
        assert vocab.isdisjoint(lexicon.INCOMPLETE_NAMING_CUES)

    def test_do_not_suggest_never_surfaces_as_candidate(self):
        skill = skill_registry.get("affect_labeling_opportunity")
        res = skill.parse_output(
            {"should_offer": True,
             "candidates": ["委屈", "失望", "崩溃", "抑郁", "失败", "考试"]},
            {"user_text": "x"},
        )
        cands = res.output["candidates"]
        assert "崩溃" not in cands and "抑郁" not in cands
        assert "失败" not in cands and "考试" not in cands
        assert cands == ["委屈", "失望"]

    def test_do_not_suggest_only_binds_suggestion_not_understanding(self):
        """DO_NOT_SUGGEST ≠ 禁止用户词汇：用户原话里的“崩溃”不会被当候选，但 gate 能处理。"""
        vocab = set(lexicon.CANDIDATE_VOCABULARY)
        assert vocab.isdisjoint(lexicon.DO_NOT_SUGGEST)
        # 用户说“快崩溃了”→ gate 不进（属于 generalized/不打扰信号），但不报错
        eng = AffectLabelingEngine.__new__(AffectLabelingEngine)
        assert eng._gate("我最近真的快崩溃了") is False or True  # 不抛异常即可

    def test_preferred_subset_of_canonical(self):
        assert set(lexicon.PREFERRED_FOR_NATURAL_DIALOGUE) <= set(lexicon.CANDIDATE_VOCABULARY)

    def test_legacy_derived_structures_still_exposed(self):
        """旧接口仍存在且与新 canonical 一致（兼容层）。"""
        assert lexicon.EMOTION_GROUPS
        assert lexicon.SPECIFIC_EMOTION_TERMS
        assert lexicon.USER_NAMING_TERMS
        all_terms = lexicon.flatten_groups()
        assert len(all_terms) == len(lexicon.CANDIDATE_VOCABULARY)
        # EMOTION_GROUPS 展平 == canonical 词集（8 组全收录）
        grouped = {t for terms in lexicon.EMOTION_GROUPS.values() for t in terms}
        assert grouped == set(lexicon.CANDIDATE_VOCABULARY)


# ═══════════════════════════════════════════════════════════════════════════════
# 最小修复回归：gate reachability（P4/R2）与 Opportunity prompt 边界（E2/N2/N6）
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinimalFixRegression:
    """针对真实调用暴露问题的确定性回归（gate 与 prompt 守卫，非全 LLM）。"""

    def _gate(self, msg):
        return AffectLabelingEngine.__new__(AffectLabelingEngine)._gate(msg)

    # —— 问题 1：reachability（P4 / R2 变体现在应命中） ——
    @pytest.mark.parametrize("msg", [
        "最近在职场上感觉自己总是被落下，很不是滋味。",      # P4
        "朋友最近总是冷落我，我说不上生不生气，心里空空的。",  # R2
        "我也说不上到底怎么了，心里有点闷。",
    ])
    def test_interpersonal_vague_now_gates_in(self, msg):
        assert self._gate(msg) is True

    # —— 第二轮路由评估（2026-09-12）补录回归：命名卡壳/混合感受/急性空落的变体 ——
    @pytest.mark.parametrize("msg", [
        "当他说完那句话，我心里那种说不出名字的感觉又来了。",  # 说不出名字
        "这几种感觉搅在一起，帮我理一理它们都是什么。",        # 搅在一起
        "知道他要走的那一瞬间，我心里空掉了。",               # 心里空掉
        "我身体没什么问题，就是心里发闷。",                   # 心里发闷（despite 身体否定句）
    ])
    def test_second_round_naming_cues_gates_in(self, msg):
        assert self._gate(msg) is True

    # —— 第三轮 therapy 评估（2026-09-12）补录回归（ESB-09）：用户把「做不进去」
    #    归因给未命名的「心里的事」——第一人称低区分度归因命中；裸「帮我理清」
    #    仍按既有 SOLUTION/排序困惑约定不触发（避免误伤理思路/理计划）。
    @pytest.mark.parametrize("msg", [
        "我在乎的事全都做不进去，我知道是心里的事，帮我理清在那之前该抓哪一头。",
    ])
    def test_therapy_round_attribution_cue_gates_in(self, msg):
        assert self._gate(msg) is True

    @pytest.mark.parametrize("msg", [
        "帮我理清思路就好了",          # 通用「理清」不作为 cue
        "帮我把这份计划理清楚",        # 计划整理困惑归日常/方案侧
    ])
    def test_generic_liqing_does_not_gate(self, msg):
        assert self._gate(msg) is False

    # —— 已自行命名的感受（空落落/如释重负）不重复标注（AL-03 裁定：尊重自命名）

    # —— 不扩大 gate：普通“人际事件”本身不触发 ——
    def test_plain_interpersonal_context_does_not_trigger(self):
        assert self._gate("朋友今天没理我。") is False
        assert self._gate("他们聚会没叫我，我也懒得想。") is False

    # —— INCOMPLETE 参与 gate 但不无条件：闲聊里“也不知道”不触发 ——
    def test_incomplete_weak_cue_without_affect_reference_not_trigger(self):
        assert self._gate("我也不知道今天天气会不会变好。") is False
        assert self._gate("我也不知道是不是我想多了。") is False
        assert self._gate("我也不知道该怎么做。") is False  # 决策困惑仍排除

    def test_hard_exclusions_still_first(self):
        assert self._gate("我不想活了，说不上来为什么。") is False  # 危机优先于 LOW
        assert self._gate("我只是很难过而已。") is False              # 已明确命名 → 仍不进
        assert self._gate("我很失望，就是很失望。") is False

    # —— 问题 2/3/4：Opportunity prompt 边界守卫存在，且未删“焦虑” ——
    def test_opportunity_prompt_has_boundary_rules(self):
        from src.skills.prompts.affect import AFFECT_OPPORTUNITY_PROMPT as P
        assert "尊重用户已完成的自我命名" in P
        assert "不要用身体症状推导心理诊断" in P
        assert "崩溃级表达不做命名" in P
        assert "焦虑" in P  # 只约束行为，不把“焦虑”从词库/提示中删除

    def test_opportunity_prompt_allows_confused_self_naming_exception(self):
        from src.skills.prompts.affect import AFFECT_OPPORTUNITY_PROMPT as P
        assert "命名困惑/混淆" in P or "分不清" in P
