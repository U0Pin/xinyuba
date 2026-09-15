"""Phase 3 测试：疗法机制（编排、流程推进、定量边际效用裁决、疗法决策、CBT 技能、ACT 修复）。"""

import asyncio
import json

import pytest

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
from src.core.llm_client import SimpleResp
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.core.skill import skill_registry
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.store.session_store import current_therapy_transcript
from src.utils.config import config
from tests.support.fakes import make_fake_client

import src.skills  # noqa: F401 — 注册技能

SAFETY_SAFE = json.dumps({"risk_level": "SAFE", "risk_type": "none"})
PROFILE_NOCHANGE = json.dumps({"updates": {}, "append_lists": {}, "no_change": True})
JUDGE_ADVANCE = json.dumps({"action": "advance", "target_step": None, "achieved": True, "reason": "达成"})
JUDGE_STAY = json.dumps({"action": "stay", "target_step": None, "achieved": False, "reason": "未达成"})
JUDGE_COMPLETE = json.dumps({"action": "complete", "target_step": None, "achieved": True, "reason": "满意"})

INTERVENTION_JSON = json.dumps({
    "technique": {"name": "test_tech", "description": "测试技术说明", "steps": ["第一步", "第二步"]},
    "conversation_goal": "测试对话目标",
    "adaptation": {"tone_adjustment": "温和", "pace_adjustment": "slow", "culture_note": "注意"},
    "contraindications": [{"condition": "c", "reason": "r"}],
}, ensure_ascii=False)

EXTRACT_JSON = json.dumps({
    "automatic_thoughts": [
        {"thought": "我什么都做不好", "confidence": 0.85, "span": "什么都做不好", "target": "self"},
    ],
    "primary_thought": "我什么都做不好",
    "no_automatic_thought_detected": False,
}, ensure_ascii=False)

DETECT_JSON = json.dumps({
    "classifications": [
        {"thought": "我什么都做不好",
         "distortions": [{"type": "mind_reading", "confidence": 0.9, "rationale": "r"},
                         {"type": "self_blame", "confidence": 0.75, "rationale": "r"}],
         "primary_distortion": "mind_reading"},
    ],
    "distortion_summary": {"dominant_pattern": "mind_reading", "distortion_count": 2, "severity": "moderate"},
}, ensure_ascii=False)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODEL_NAME", "test-model")
    monkeypatch.setattr(config, "CHEAP_MODEL_NAME", "test-cheap")
    monkeypatch.setattr(config, "SUMMARY_TOKEN_THRESHOLD", 10**9)
    monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)  # 各测试按需覆盖（0 = 每条消息评估）
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
        ),
        settlement_line=SettlementLine(client, session_store=sessions, profile_store=profiles),
        session_store=sessions,
        profile_store=profiles,
    )
    return {"client": client, "provider": provider, "sink": sink,
            "sessions": sessions, "scheduler": scheduler}


async def run_turn(scheduler, uid, sid, msg):
    events = []
    async for event in scheduler.handle_message(uid, sid, msg):
        events.append(event)
    return events


def set_therapy(env, sid, name):
    state = env["sessions"].read_state(sid)
    state["owner"] = f"therapy_{name.lower()}"
    state["therapy"] = {"name": name, "start_seq": 0, "rounds": 0,
                        "step_index": -1, "branch": None, "basic_flow_complete": False}
    env["sessions"].write_state(sid, state)


def script_therapy_turn(env, judgment, skill=None, extra_before_profile=None):
    """按编排调用顺序脚本化：judgment → skill → (extra) → profile。"""
    env["provider"].script_stream("test-model", [["回", "复"]])
    env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
    env["provider"].script_complete("test-model", [judgment])
    if skill:
        env["provider"].script_complete("test-model", [skill])
    if extra_before_profile:
        env["provider"].script_complete("test-model", extra_before_profile)
    env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])


class TestCbtFlow:
    def test_cbt_flow_progression_and_end(self, env):
        """识别→分类→挑战(路由 reframe)→重构→评估效果(complete)→继续评估结束。"""
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "CBT")
        sched = env["scheduler"]

        # turn1: identify_thought（advance → classify）
        script_therapy_turn(env, JUDGE_ADVANCE, EXTRACT_JSON)
        asyncio.run(run_turn(sched, "u1", sid, "我觉得我什么都做不好"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "identify_thought"
        assert orch["skill_output"]["_skill_name"] == "extract_automatic_thought"
        t = env["sessions"].read_state(sid)["therapy"]
        assert t["step_index"] == 1 and t["rounds"] == 1

        # turn2: classify_distortion
        script_therapy_turn(env, JUDGE_ADVANCE, DETECT_JSON)
        asyncio.run(run_turn(sched, "u1", sid, "同事没回我消息"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["skill_output"]["_skill_name"] == "detect_cognitive_distortion"
        assert orch["products"]["distortions"][0]["distortions"][0]["type"] == "mind_reading"
        assert env["sessions"].read_state(sid)["therapy"]["step_index"] == 2

        # turn3: challenge —— mind_reading 路由到 generate_reframe
        script_therapy_turn(env, JUDGE_ADVANCE, INTERVENTION_JSON)
        asyncio.run(run_turn(sched, "u1", sid, "我觉得他讨厌我"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["skill_output"]["_skill_name"] == "generate_reframe"
        assert env["sessions"].read_state(sid)["therapy"]["step_index"] == 3

        # turn4: reframe → evaluate_effect
        script_therapy_turn(env, JUDGE_ADVANCE, INTERVENTION_JSON)
        asyncio.run(run_turn(sched, "u1", sid, "也许他有事在忙"))
        t = env["sessions"].read_state(sid)["therapy"]
        assert t["step_index"] == 4 and t["basic_flow_complete"] is False

        # turn5: evaluate_effect（无 skill）→ complete：里程碑记录，效用裁决仍判"持续"
        # （B1：结束不再由 LLM 投票；complete 是有收益轮）
        script_therapy_turn(env, JUDGE_COMPLETE)
        asyncio.run(run_turn(sched, "u1", sid, "这样想好多了"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["skill_output"] is None
        assert orch["utility_eval"]["stop"] is False      # 效用裁决：有推进就不停
        t = env["sessions"].read_state(sid)["therapy"]
        assert t["basic_flow_complete"] is True           # 里程碑仍在
        assert t["utility_history"][-1]["action"] == "complete"
        state = env["sessions"].read_state(sid)
        assert state["therapy"] is not None               # 完成后没有 LLM 一票否决了

        # turn6-7: 连续两轮无收益（stay + judgment_only 步 + 状态信号缺失）→ 停滞判停
        script_therapy_turn(env, JUDGE_STAY)
        asyncio.run(run_turn(sched, "u1", sid, "嗯，好像轻松了一点"))
        script_therapy_turn(env, JUDGE_STAY)
        asyncio.run(run_turn(sched, "u1", sid, "也没什么新的了"))
        state = env["sessions"].read_state(sid)
        assert state["therapy"] is None
        assert state["owner"] == "daily"
        assert state["last_therapy"] == {"name": "CBT", "rounds": 7,
                                         "ended_reason": "stagnation"}

    def test_round_cap_ends_therapy(self, env, monkeypatch):
        monkeypatch.setattr(config, "THERAPY_MAX_ROUNDS", 2)
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "CBT")
        script_therapy_turn(env, JUDGE_ADVANCE, EXTRACT_JSON)
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "a"))
        script_therapy_turn(env, JUDGE_ADVANCE, DETECT_JSON)
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "b"))
        state = env["sessions"].read_state(sid)
        assert state["therapy"] is None  # round_cap 结束
        assert state["owner"] == "daily"

    def test_go_back_to_challenge(self, env):
        """评估效果不满意 → go_to challenge。"""
        sid = env["sessions"].create("u1")
        state = env["sessions"].read_state(sid)
        state["owner"] = "therapy_cbt"
        state["therapy"] = {"name": "CBT", "start_seq": 0, "rounds": 4,
                            "step_index": 4, "branch": None, "basic_flow_complete": False}
        env["sessions"].write_state(sid, state)
        script_therapy_turn(env, json.dumps({"action": "go_to", "target_step": "challenge",
                                             "achieved": False, "reason": "不满意"}), None)
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "可我还是觉得他讨厌我"))
        t = env["sessions"].read_state(sid)["therapy"]
        assert t["step_index"] == 2  # 回到 challenge
        assert t["basic_flow_complete"] is False


class TestTherapyRouting:
    def test_act_identity_fusion_routes_to_self_as_context(self, env):
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "ACT")
        assessment = json.dumps({
            "fusion": {"index": 0.9, "state": "cf_identity_fusion", "confidence": 0.8, "evidence": []},
            "avoidance": {"index": 0.4, "state": "ea_avoiding", "confidence": 0.5, "evidence": []},
            "openness": {"index": 0.4, "state": "eo_guarded", "confidence": 0.5, "evidence": []},
            "alignment": {"index": 0.5, "state": "medium", "confidence": 0.5, "values_mentioned": []},
            "activation": {"index": 0.5, "state": "medium", "confidence": 0.5},
            "overall_trend": "stable", "escalation_level": "baseline",
            "primary_concern": "fusion", "should_intervene": True,
            "recommended_processes": [{"process": "defusion", "priority": 1, "rationale": "r"}],
        }, ensure_ascii=False)
        env["provider"].script_stream("test-model", [["回"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [assessment, JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我就是一个失败的人"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "self_as_context"
        assert orch["skill_output"]["_skill_name"] == "act_self_as_context"

    def test_mi_change_talk_routes_to_evocation(self, env):
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "MI")
        assessment = json.dumps({
            "change_readiness": {"index": 0.4, "state": "contemplation", "confidence": 0.7, "evidence": []},
            "ambivalence": {"index": 0.5, "state": "moderate", "confidence": 0.7, "evidence": []},
            "self_efficacy": {"index": 0.2, "state": "very_low", "confidence": 0.6, "evidence": []},
            "change_talk": {"index": 0.4, "state": "weak", "confidence": 0.6, "evidence": []},
            "sustain_talk": {"index": 0.3, "state": "weak", "confidence": 0.6, "evidence": []},
            "overall_trend": "stable", "primary_concern": "change_talk",
            "should_intervene": True,
            "recommended_processes": [{"process": "evocation", "priority": 1, "rationale": "r"}],
        }, ensure_ascii=False)
        env["provider"].script_stream("test-model", [["回"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [assessment, JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我想改，但就是做不到"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "evocation"
        assert orch["skill_output"]["_skill_name"] == "mi_evocation"

    def test_sfbt_problem_stuck_routes_to_exception(self, env):
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "SFBT")
        assessment = json.dumps({
            "goal_clarity": {"index": 0.3, "state": "vague", "confidence": 0.7, "evidence": []},
            "resource_awareness": {"index": 0.5, "state": "moderate", "confidence": 0.6, "evidence": []},
            "problem_orientation": {"index": 0.8, "state": "problem_stuck", "confidence": 0.7, "evidence": []},
            "overall_trend": "stable", "primary_concern": "problem_stuck",
            "should_intervene": True,
            "recommended_processes": [{"process": "exception_exploration", "priority": 1, "rationale": "r"}],
        }, ensure_ascii=False)
        env["provider"].script_stream("test-model", [["回"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [assessment, JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我卡住了，一直焦虑"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "exception_exploration"
        assert orch["skill_output"]["_skill_name"] == "sfbt_exception_exploration"

    def test_dbt_recommended_order(self, env):
        sid = env["sessions"].create("u1")
        set_therapy(env, sid, "DBT")
        assessment = json.dumps({
            "emotion_dysregulation": {"index": 0.7, "state": "severe", "confidence": 0.8, "evidence": []},
            "distress_level": {"index": 0.8, "state": "high", "confidence": 0.7, "evidence": []},
            "impulsivity": {"index": 0.3, "state": "controlled", "confidence": 0.6, "evidence": []},
            "interpersonal_conflict": {"index": 0.5, "state": "moderate", "confidence": 0.5, "evidence": []},
            "mindfulness_awareness": {"index": 0.3, "state": "low", "confidence": 0.5, "evidence": []},
            "overall_trend": "worsening", "primary_concern": "distress",
            "should_intervene": True,
            "recommended_processes": [
                {"process": "distress_tolerance", "priority": 1, "rationale": "r"},
                {"process": "mindfulness", "priority": 2, "rationale": "r"},
            ],
        }, ensure_ascii=False)
        env["provider"].script_stream("test-model", [["回"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [assessment, JUDGE_ADVANCE, INTERVENTION_JSON, PROFILE_NOCHANGE])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我快撑不住了"))
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "distress_tolerance"
        # advance 后进入下一个推荐模块 mindfulness（而非线性下一个 emotion_regulation）
        t = env["sessions"].read_state(sid)["therapy"]
        assert t["step_index"] == 0  # mindfulness 的 FLOW 下标


class TestTherapyDecider:
    def test_switch_to_therapy_signal(self, env, monkeypatch):
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)  # 每条消息都评估
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "MI", "reason": "持续矛盾"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我知道应该改，但就是做不到"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "therapy_mi"
        assert state["therapy"]["name"] == "MI"

    def test_next_turn_therapy_dialogue(self, env, monkeypatch):
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        # turn1: 决策 → MI
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "MI", "reason": "r"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我想改但不想开始"))
        # turn2: 疗法接管（对话线用疗法 prompt）
        env["provider"].script_stream("test-model", [["回"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"change_readiness": {"index": 0.3}, "ambivalence": {"index": 0.7},
                        "self_efficacy": {"index": 0.4}, "change_talk": {"index": 0.1},
                        "sustain_talk": {"index": 0.5}, "recommended_processes":
                        [{"process": "explore", "priority": 1, "rationale": "r"}]}),
            JUDGE_STAY, INTERVENTION_JSON, PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "我也不知道自己想不想改"))
        stream_prompts = [c[1] for c in env["provider"].calls if c[0] == "stream"]
        assert "MI 动机式访谈" in stream_prompts[-1]
        orch = env["sessions"].read_orchestration(sid)
        assert orch["current_step"] == "explore"
        assert orch["skill_output"]["_skill_name"] == "mi_explore"
        # 流水标记：therapy 段与 transcript 工具
        flow = env["sessions"].read_flow_all(sid)
        assert flow[-1]["owner"] == "therapy_mi"
        transcript = current_therapy_transcript(env["sessions"], sid)
        assert [e["owner"] for e in transcript] == ["therapy_mi", "therapy_mi"]

    def test_threshold_blocks_decision(self, env, monkeypatch):
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 10**9)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [PROFILE_NOCHANGE])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "随便聊聊，今天天气不错"))
        agents = {r["agent"] for r in env["sink"].records}
        assert "therapy_decider" not in agents
        assert env["sessions"].read_state(sid)["pending_user_tokens"] > 0


class TestTherapyAntiHogging:
    """防抢占测试：验证 MI/SFBT 不会大包大揽地抢占 CBT/ACT/DBT 的空间。

    两层防线：
    1. Prompt 内容守卫 — THERAPY_DECISION_PROMPT 必须包含防抢占规则；
    2. 管道路由覆盖 — decider 对 5 种疗法都能正确路由（不只 MI）。
    """

    # ── Prompt 内容守卫 ──────────────────────────────────────────

    def test_prompt_has_cbt_over_mi_rule(self):
        """规则 3：认知扭曲是强 CBT 信号，不应因轻微改变意愿选 MI。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        assert "不要因为附带了轻微改变意愿就选 MI" in THERAPY_DECISION_PROMPT

    def test_prompt_has_dbt_over_mi_rule(self):
        """规则 4：情绪失控优先 DBT，不绕过 DBT 选 MI。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        assert "不要绕过 DBT 去选 MI" in THERAPY_DECISION_PROMPT

    def test_prompt_has_sfbt_signal_requirement(self):
        """规则 5：SFBT 需要明确的资源/例外/愿景信号，仅'想改变'不是 SFBT。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        assert "不是 SFBT" in THERAPY_DECISION_PROMPT
        assert "更接近 MI" in THERAPY_DECISION_PROMPT

    def test_prompt_has_mi_vs_sfbt_boundary(self):
        """MI 处理动机矛盾，SFBT 处理方案落地——两者边界清晰。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        assert "MI 处理" in THERAPY_DECISION_PROMPT
        assert "SFBT 处理" in THERAPY_DECISION_PROMPT

    def test_prompt_has_conflict_examples(self):
        """Prompt 包含冲突判断示例，防止 MI/SFBT 误判。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        # mind_reading → CBT 而非 MI
        assert "他肯定对我有意见" in THERAPY_DECISION_PROMPT
        assert "CBT" in THERAPY_DECISION_PROMPT

    def test_prompt_has_no_default_priority(self):
        """按信号最强匹配，不默认任何疗法。"""
        from src.agents.prompts.therapy import THERAPY_DECISION_PROMPT
        assert "没有固定优先级" in THERAPY_DECISION_PROMPT

    # ── 管道路由覆盖：decider 对 5 种疗法都能正确路由 ────────────

    @pytest.mark.parametrize("therapy_name", ["CBT", "ACT", "DBT", "MI", "SFBT"])
    def test_decider_routes_all_five_therapies(self, env, monkeypatch, therapy_name):
        """每种疗法都能被 decider 正确路由到（不只 MI）。"""
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": therapy_name, "reason": "test"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "test message"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == f"therapy_{therapy_name.lower()}"
        assert state["therapy"]["name"] == therapy_name

    # ── 冲突场景：验证 decider 管道在非 MI/SFBT 决策时也工作 ────

    def test_cbt_distortion_with_minor_change_talk_routes_to_cbt(self, env, monkeypatch):
        """认知扭曲 + 轻微改变意愿 → CBT（不是 MI）。

        场景：用户说"他肯定对我有意见，我想改改这个毛病"
        prompt 规则 3 明确要求选 CBT 而非 MI。
        """
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "CBT",
                        "reason": "mind_reading 认知扭曲，强 CBT 信号"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid,
                             "他肯定对我有意见，我想改改这个毛病"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "therapy_cbt"
        assert state["therapy"]["name"] == "CBT"

    def test_dbt_emotion_crisis_routes_to_dbt(self, env, monkeypatch):
        """情绪失控 → DBT（不是 MI）。

        场景：用户说"我快崩溃了，什么都做不了"
        prompt 规则 4 明确要求优先 DBT。
        """
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "DBT",
                        "reason": "情绪失控，强 DBT 信号"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid,
                             "我快崩溃了，什么都做不了"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "therapy_dbt"
        assert state["therapy"]["name"] == "DBT"

    def test_act_identity_fusion_routes_to_act(self, env, monkeypatch):
        """身份级融合 → ACT（不是 MI）。

        场景：用户说"我就是一个失败者"
        prompt 冲突示例明确要求选 ACT。
        """
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "ACT",
                        "reason": "身份级融合，强 ACT 信号"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid,
                             "我就是一个失败者"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "therapy_act"
        assert state["therapy"]["name"] == "ACT"

    def test_sfbt_resource_signal_routes_to_sfbt(self, env, monkeypatch):
        """资源信号 → SFBT（不是 MI）。

        场景：用户说"我以前也撑过来过，想找回那次的方法"
        prompt 冲突示例明确要求选 SFBT。
        """
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "SFBT",
                        "reason": "资源信号，强 SFBT 信号"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid,
                             "我以前也撑过来过，想找回那次的方法"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "therapy_sfbt"
        assert state["therapy"]["name"] == "SFBT"

    def test_no_therapy_for_casual_chat(self, env, monkeypatch):
        """闲聊 → 不需要疗法（MI/SFBT 都不选）。"""
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": False, "therapy": None, "reason": "闲聊"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "今天天气不错"))
        state = env["sessions"].read_state(sid)
        assert state["owner"] == "daily"
        assert state["therapy"] is None

    # ── Prompt 构建验证：发给 LLM 的 prompt 包含防抢占规则 ──────

    def test_decider_prompt_sent_to_llm_contains_anti_hogging_rules(self, env, monkeypatch):
        """发给 LLM 的实际 prompt 包含所有防抢占规则。"""
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": False, "therapy": None, "reason": "r"}),
            PROFILE_NOCHANGE,
        ])
        asyncio.run(run_turn(env["scheduler"], "u1", sid, "test"))
        decider_calls = [c for c in env["provider"].calls
                         if c[0] == "complete" and c[2] == "test-model"
                         and "疗法选择模块" in c[1]]
        assert len(decider_calls) == 1
        prompt = decider_calls[0][1]
        # 验证防抢占规则都在发给 LLM 的 prompt 中
        assert "不要因为附带了轻微改变意愿就选 MI" in prompt
        assert "不要绕过 DBT 去选 MI" in prompt
        assert "没有固定优先级" in prompt


class TestTriggerSemantics:
    """触发语义：评估频率（阈值）≠ 触发条件。是否触发始终由决策器按信号判断。"""

    def test_casual_chat_evaluated_but_not_triggered(self, env, monkeypatch):
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)  # 每条消息都评估
        sid = env["sessions"].create("u1")
        for msg in ["你好呀", "今天天气不错，随便聊聊"]:
            env["provider"].script_stream("test-model", [["好"]])
            env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
            env["provider"].script_complete("test-model", [
                json.dumps({"need_therapy": False, "therapy": None, "reason": "闲聊，无需干预"}),
                PROFILE_NOCHANGE,
            ])
            asyncio.run(run_turn(env["scheduler"], "u1", sid, msg))
            state = env["sessions"].read_state(sid)
            assert state["owner"] == "daily"
            assert state["therapy"] is None
        # 评估确实每轮都在跑（决策器被调用两次），但都没触发
        decider_calls = [c for c in env["provider"].calls
                         if c[0] == "complete" and c[2] == "test-model" and "疗法选择模块" in c[1]]
        assert len(decider_calls) == 2


class TestTalksLikeHit:
    """talks 黑盒风格的命中验证：DEBUG_TALKS 下 final 事件报告 planned_skill / current_therapy。"""

    def test_debug_fields_report_therapy_hit(self, env, monkeypatch):
        monkeypatch.setenv("DEBUG_TALKS", "1")
        monkeypatch.setattr(config, "WINDOW_TOKEN_THRESHOLD", 0)
        sid = env["sessions"].create("u1")
        # turn1: 疗法决策 → CBT
        env["provider"].script_stream("test-model", [["好"]])
        env["provider"].script_complete("test-cheap", [SAFETY_SAFE])
        env["provider"].script_complete("test-model", [
            json.dumps({"need_therapy": True, "therapy": "CBT", "reason": "认知扭曲"}),
            PROFILE_NOCHANGE,
        ])
        events1 = asyncio.run(run_turn(env["scheduler"], "u1", sid, "他肯定对我有意见"))
        final1 = events1[-1]["data"]
        # final 反映轮边界应用信号后的调度状态：切换已排定、下一轮生效
        assert final1["current_therapy"] == "CBT"
        assert final1["planned_skill"] is None

        # turn2: 疗法接管 → 首步 identify_thought → extract_automatic_thought 命中
        script_therapy_turn(env, JUDGE_STAY, EXTRACT_JSON)
        events2 = asyncio.run(run_turn(env["scheduler"], "u1", sid, "我觉得我什么都做不好"))
        final2 = events2[-1]["data"]
        assert final2["current_therapy"] == "CBT"
        assert final2["planned_skill"] == "extract_automatic_thought"
        assert final2["intervention_count"] == 1


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return SimpleResp(self.responses.pop(0))


class TestCbtAnalysisSkills:
    def test_extract_parses_valid(self):
        skill = skill_registry.get("extract_automatic_thought")
        llm = _FakeLLM([EXTRACT_JSON])
        res = skill.execute({"user_text": "我什么都做不好"}, {"llm": llm})
        assert res.success is True
        assert res.output["primary_thought"] == "我什么都做不好"
        assert res.output["automatic_thoughts"][0]["target"] == "self"
        assert len(llm.prompts) == 1 and "automatic thought" in llm.prompts[0]

    def test_extract_empty_falls_back_to_error(self):
        skill = skill_registry.get("extract_automatic_thought")
        llm = _FakeLLM(["{}"])
        res = skill.execute({"user_text": "hi"}, {"llm": llm})
        assert res.success is False  # LLMSkill 默认 fallback（与其它评估技能一致）

    def test_detect_parses_valid_and_filters_types(self):
        skill = skill_registry.get("detect_cognitive_distortion")
        bad = json.dumps({"classifications": [
            {"thought": "t", "distortions": [{"type": "bogus", "confidence": 1.0}],
             "primary_distortion": None}]})
        llm = _FakeLLM([DETECT_JSON, bad])
        res = skill.execute({"automatic_thoughts": [{"thought": "x"}]}, {"llm": llm})
        assert res.output["classifications"][0]["distortions"][0]["type"] == "mind_reading"
        assert res.output["distortion_summary"]["distortion_count"] == 2
        res2 = skill.execute({"automatic_thoughts": [{"thought": "t"}]}, {"llm": llm})
        assert res2.output["classifications"][0]["distortions"] == []  # 非法类型被过滤

    def test_detect_empty_thoughts_skips_llm(self):
        skill = skill_registry.get("detect_cognitive_distortion")
        llm = _FakeLLM([])
        res = skill.execute({"automatic_thoughts": []}, {"llm": llm})
        assert res.success is True
        assert res.output["classifications"] == []
        assert llm.prompts == []


class TestCbtProductsPersistence:
    """CBT products 跨轮持久化语义测试（修复 products_update 覆盖 bug）。"""

    def _agent(self):
        """直接实例化 CbtTherapyAgent，绕过 __init__（不依赖 LLM）。"""
        return CbtTherapyAgent.__new__(CbtTherapyAgent)

    def _extract_skill(self, *, thoughts=None, primary="", no_detected=False):
        """构造 extract_automatic_thought skill 输出。"""
        return {
            "_skill_name": "extract_automatic_thought",
            "automatic_thoughts": thoughts if thoughts is not None else [],
            "primary_thought": primary,
            "no_automatic_thought_detected": no_detected,
        }

    def _classify_skill(self, *, classifications=None, summary=None):
        """构造 detect_cognitive_distortion skill 输出。"""
        return {
            "_skill_name": "detect_cognitive_distortion",
            "classifications": classifications if classifications is not None else [],
            "distortion_summary": summary if summary is not None else {
                "dominant_pattern": None,
                "distortion_count": 0,
                "severity": "none",
            },
        }

    def test_empty_skill_output_does_not_overwrite_existing_thoughts(self):
        """Test 1：空 skill 输出不能覆盖已有 automatic_thoughts / primary_thought。"""
        agent = self._agent()
        products = {
            "automatic_thoughts": [{"thought": "我不够好", "confidence": 0.9}],
            "primary_thought": "我不够好",
            "no_automatic_thought_detected": False,
        }
        skill_output = self._extract_skill(thoughts=[], primary="", no_detected=True)
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=skill_output,
            products=products,
        )
        assert len(products["automatic_thoughts"]) == 1
        assert products["automatic_thoughts"][0]["thought"] == "我不够好"
        assert products["primary_thought"] == "我不够好"
        # 关键：no_automatic_thought_detected 不被错误置为 True
        assert products["no_automatic_thought_detected"] is False

    def test_non_empty_skill_output_updates_and_merges(self):
        """Test 2：非空 skill 输出正常更新；与已有 thoughts 合并去重。"""
        agent = self._agent()
        products = {
            "automatic_thoughts": [{"thought": "old", "confidence": 0.8}],
            "primary_thought": "old",
            "no_automatic_thought_detected": False,
        }
        skill_output = self._extract_skill(
            thoughts=[{"thought": "new", "confidence": 0.9}],
            primary="new",
            no_detected=False,
        )
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=skill_output,
            products=products,
        )
        # 合并：old + new
        thoughts_text = [t["thought"] for t in products["automatic_thoughts"]]
        assert "old" in thoughts_text and "new" in thoughts_text
        assert len(products["automatic_thoughts"]) == 2
        # primary_thought 被新值覆盖
        assert products["primary_thought"] == "new"

    def test_cross_round_accumulation_with_dedup(self):
        """Test 3：跨轮累积 + 去重。"""
        agent = self._agent()

        # T1 → 提取 ['我不够好']
        products = {"automatic_thoughts": [], "primary_thought": "", "no_automatic_thought_detected": False}
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=self._extract_skill(
                thoughts=[{"thought": "我不够好", "confidence": 0.9}], primary="我不够好"
            ),
            products=products,
        )
        assert [t["thought"] for t in products["automatic_thoughts"]] == ["我不够好"]

        # T2 → 提取 ['我当时应该说那句话']
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=self._extract_skill(
                thoughts=[{"thought": "我当时应该说那句话", "confidence": 0.9}],
                primary="我当时应该说那句话",
            ),
            products=products,
        )
        assert [t["thought"] for t in products["automatic_thoughts"]] == [
            "我不够好", "我当时应该说那句话",
        ]

        # T3 → 重复 ['我不够好']，去重
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=self._extract_skill(
                thoughts=[{"thought": "我不够好", "confidence": 0.9}], primary=""
            ),
            products=products,
        )
        assert [t["thought"] for t in products["automatic_thoughts"]] == [
            "我不够好", "我当时应该说那句话",
        ]

        # T4 → 空输出，不应清空已有
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=self._extract_skill(thoughts=[], primary="", no_detected=True),
            products=products,
        )
        assert [t["thought"] for t in products["automatic_thoughts"]] == [
            "我不够好", "我当时应该说那句话",
        ]

    def test_empty_distortions_does_not_overwrite_existing(self):
        """Test 4：classifications 空输出不能覆盖已有 distortions。"""
        agent = self._agent()
        products = {
            "distortions": [
                {"thought": "我不够好", "distortions": [
                    {"type": "self_blame", "confidence": 0.9, "rationale": "自我责备"}
                ], "primary_distortion": "self_blame"},
            ],
            "distortion_summary": {"dominant_pattern": "self_blame", "distortion_count": 1, "severity": "moderate"},
        }
        skill_output = self._classify_skill()  # 空
        agent.products_update(
            step_name="classify_distortion",
            skill_name="detect_cognitive_distortion",
            skill_output=skill_output,
            products=products,
        )
        assert len(products["distortions"]) == 1
        assert products["distortions"][0]["primary_distortion"] == "self_blame"
        assert products["distortion_summary"]["dominant_pattern"] == "self_blame"

    def test_distortions_accumulation_with_dedup(self):
        """classify_distortion 跨轮累积按 (thought, type) 去重。"""
        agent = self._agent()
        products = {"distortions": [], "distortion_summary": {}}

        # T1: 识别 1 个扭曲
        agent.products_update(
            step_name="classify_distortion",
            skill_name="detect_cognitive_distortion",
            skill_output=self._classify_skill(
                classifications=[
                    {"thought": "我不够好", "distortions": [
                        {"type": "self_blame", "confidence": 0.9, "rationale": ""}
                    ], "primary_distortion": "self_blame"},
                ],
                summary={"dominant_pattern": "self_blame", "distortion_count": 1, "severity": "moderate"},
            ),
            products=products,
        )
        assert len(products["distortions"]) == 1

        # T2: 重复同样的扭曲 → 不重复
        agent.products_update(
            step_name="classify_distortion",
            skill_name="detect_cognitive_distortion",
            skill_output=self._classify_skill(
                classifications=[
                    {"thought": "我不够好", "distortions": [
                        {"type": "self_blame", "confidence": 0.8, "rationale": ""}
                    ], "primary_distortion": "self_blame"},
                ],
            ),
            products=products,
        )
        assert len(products["distortions"]) == 1

        # T3: 新扭曲
        agent.products_update(
            step_name="classify_distortion",
            skill_name="detect_cognitive_distortion",
            skill_output=self._classify_skill(
                classifications=[
                    {"thought": "他一定在否定我", "distortions": [
                        {"type": "mind_reading", "confidence": 0.85, "rationale": ""}
                    ], "primary_distortion": "mind_reading"},
                ],
                summary={"dominant_pattern": "mind_reading", "distortion_count": 2, "severity": "moderate"},
            ),
            products=products,
        )
        assert len(products["distortions"]) == 2

    def test_thought_string_format_supported(self):
        """automatic_thoughts 也支持纯字符串列表（向后兼容）。"""
        agent = self._agent()
        products = {"automatic_thoughts": ["old thought"], "primary_thought": "old"}
        agent.products_update(
            step_name="identify_thought",
            skill_name="extract_automatic_thought",
            skill_output=self._extract_skill(
                thoughts=["new thought"], primary="new"
            ),
            products=products,
        )
        assert "old thought" in products["automatic_thoughts"]
        assert "new thought" in products["automatic_thoughts"]
        assert len(products["automatic_thoughts"]) == 2


class TestActEmotionalIntensityFix:
    def test_emotional_intensity_reads_flat_arousal(self):
        skill = skill_registry.get("act_state_assessment")
        assessment = json.dumps({
            "fusion": {"index": 0.5, "confidence": 0.5},
            "avoidance": {"index": 0.4, "confidence": 0.5},
            "openness": {"index": 0.5, "confidence": 0.5},
            "alignment": {"index": 0.5, "confidence": 0.5},
            "activation": {"index": 0.5, "confidence": 0.5},
            "recommended_processes": [],
        })
        llm = _FakeLLM([assessment])
        res = skill.execute(
            {"user_text": "hi", "history": [], "emotion": {"arousal": 0.6},
             "profile": {}, "current_act_state": None},
            {"llm": llm},
        )
        assert res.output["emotional_intensity"] == 0.6
        assert res.output["escalation_level"] == "escalating"
