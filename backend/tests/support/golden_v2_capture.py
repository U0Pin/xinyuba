"""新架构黄金快照捕获（Phase 2/3）。

捕获新架构的全部 LLM prompt 面：
- Phase 2：日常对话（普通/危机）、安全决策、画像增量、摘要生成；
- Phase 3：疗法决策、步骤推进判断（五疗法）、疗法对话模板（五疗法，
  经 Host 装配）、CBT 两个分析技能的 prompt。

任何改动若意外改变这些 prompt，test_golden_v2_snapshots 必炸。

重新生成（在 agent/ 下运行）：
    ../.venv/bin/python tests/support/golden_v2_capture.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.skills  # noqa: E402,F401 — 注册全部技能（skill_registry 依赖）

from src.agents.host_agent import HostDialogueAgent  # noqa: E402
from src.agents.safety_agent import SafetyAgent  # noqa: E402
from src.agents.settlement import (  # noqa: E402
    build_profile_update_prompt,
    build_summary_prompt,
)
from src.agents.therapy import (  # noqa: E402
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.agents.therapy_decider import TherapyDecider  # noqa: E402
from src.core.skill import skill_registry  # noqa: E402

USER_TEXT = "我最近总是失眠，一想到工作的事就心慌，觉得自己什么都做不好。"

PROFILE = {
    "attachment_style": "anxious",
    "core_fears": ["被拒绝"],
    "personal_values": ["被认可"],
}

SUMMARY = {
    "updated_seq": 3,
    "token_estimate": 120,
    "summary": [
        {"theme": "工作压力", "fact": "连续数月工作压力大", "emotion": "焦虑", "seq_range": [1, 3]}
    ],
}

RECENT = [
    {"seq": 1, "role": "user", "owner": "daily", "text": "你好"},
    {"seq": 2, "role": "agent", "owner": "daily", "text": "你好呀"},
    {"seq": 3, "role": "user", "owner": "daily", "text": "我工作压力很大"},
]

NEW_ENTRIES = [
    {"seq": 4, "role": "agent", "owner": "daily", "text": "听起来工作让你很疲惫。"},
    {"seq": 5, "role": "user", "owner": "daily", "text": "嗯，已经好几个月了"},
]

THERAPY_TRANSCRIPT = [
    {"seq": 10, "role": "user", "owner": "therapy_cbt", "text": "我觉得我什么都做不好"},
    {"seq": 11, "role": "agent", "owner": "therapy_cbt", "text": "我们来看看这个想法"},
]

CANON_ASSESSMENT = {
    "fusion": {"index": 0.72, "state": "cf_fused", "confidence": 0.8, "evidence": ["e"]},
    "avoidance": {"index": 0.55, "state": "ea_avoiding", "confidence": 0.7, "evidence": ["e"]},
    "openness": {"index": 0.3, "state": "eo_guarded", "confidence": 0.6, "evidence": ["e"]},
    "alignment": {"index": 0.25, "state": "low", "confidence": 0.5, "values_mentioned": []},
    "activation": {"index": 0.2, "state": "low", "confidence": 0.55},
    "escalation_level": "baseline",
    "recommended_processes": [{"process": "defusion", "priority": 1, "rationale": "r"}],
}

CANON_ORCHESTRATION = {
    "updated_seq": 2,
    "therapy": "CBT",
    "current_step": "challenge",
    "step_judgment": {"action": "stay", "target_step": None, "achieved": False, "reason": "继续挑战"},
    "skill_output": {
        "_skill_name": "generate_reframe",
        "technique": {"name": "alternative_causes", "description": "寻找其他可能原因", "steps": ["列原因", "检验"]},
        "conversation_goal": "扩展解读菜单",
        "adaptation": {"tone_adjustment": "好奇", "pace_adjustment": "normal", "culture_note": "注意"},
        "contraindications": [],
    },
    "products": {"primary_thought": "我什么都做不好", "distortions": []},
    "utility_eval": None,
}

CANON_PRODUCTS = {"primary_thought": "我什么都做不好", "distortions": []}


def _agent(cls):
    return cls.__new__(cls)


def capture() -> dict:
    host = _agent(HostDialogueAgent)
    safety = _agent(SafetyAgent)
    decider = _agent(TherapyDecider)

    out = {
        "daily_normal": host.build_prompt(
            crisis=False, risk_level="SAFE", profile=PROFILE,
            summary=SUMMARY, recent=RECENT, user_text=USER_TEXT,
        ),
        "daily_crisis": host.build_prompt(
            crisis=True, risk_level="CRISIS", profile=PROFILE,
            summary=SUMMARY, recent=RECENT, user_text=USER_TEXT,
        ),
        "safety": safety.build_prompt(user_text=USER_TEXT, current_risk="LOW_RISK"),
        "profile_update": build_profile_update_prompt(
            PROFILE, USER_TEXT, "听起来工作让你很疲惫。"
        ),
        "summary": build_summary_prompt(SUMMARY["summary"], NEW_ENTRIES),
        "therapy_decision": decider.build_prompt(
            window=RECENT, profile=PROFILE, summary=SUMMARY, user_text=USER_TEXT,
        ),
    }

    for cls in (CbtTherapyAgent, ActTherapyAgent, DbtTherapyAgent, MiTherapyAgent, SfbtTherapyAgent):
        agent = _agent(cls)
        name = cls.therapy_name.lower()
        out[f"judgment_{name}"] = agent.build_step_judgment_prompt(
            step_name=agent.FLOW[0].name, assessment=CANON_ASSESSMENT,
            products=CANON_PRODUCTS, transcript=THERAPY_TRANSCRIPT, therapy_rounds=1,
        )
        # 对话侧模板已迁至 Host（疗法 Agent 为纯分析 subAgent）；预期字节不变。
        out[f"dialogue_{name}"] = host.build_therapy_dialogue_prompt(
            therapy_name=cls.therapy_name, profile=PROFILE, summary=SUMMARY,
            transcript=THERAPY_TRANSCRIPT, orchestration=CANON_ORCHESTRATION,
            user_text=USER_TEXT,
        )

    # CBT 两个分析技能的 prompt
    out["skill_extract_prompt"] = skill_registry.get("extract_automatic_thought").build_prompt(
        {"user_text": USER_TEXT}
    )
    out["skill_detect_prompt"] = skill_registry.get("detect_cognitive_distortion").build_prompt(
        {"automatic_thoughts": [{"thought": "我什么都做不好", "confidence": 0.8, "target": "self"}]}
    )
    return out


def main() -> None:
    snap = capture()
    out = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "golden",
        "snapshots.json"
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"written {out}")


if __name__ == "__main__":
    main()
