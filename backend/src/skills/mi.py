"""MI（动机式访谈）技能实现。

4 个技能：
- 1 个 ANALYSIS（LLM 驱动）：mi_state_assessment —— 5 维评估，
  其中 `mi_intervention_need` 由后端公式计算（不由 LLM 生成）
- 3 个 INTERVENTION（LLM 驱动）：mi_explore、mi_evocation、mi_commitment

LLM 用的 prompt / output schema / 状态阈值在 `src.skills.prompts.mi`，
本文件只展示 skill 类的骨架与注册逻辑。
"""

import json

from src.core.skill import (
    LLMSkill,
    SkillResult,
    SkillType,
    TherapyInterventionSkill,
    discrete_state,
    format_history,
    skill_registry,
)
from src.core.act_state import InteractionStyle

from src.skills.techniques import (
    MIProcess,
    MIExploreTechnique,
    MIEvocationTechnique,
    MICommitmentTechnique,
)
from src.skills.prompts.mi import (
    ASSESSMENT_PROMPT, ASSESSMENT_OUTPUT_SCHEMA,
    EXPLORE_PROMPT, EXPLORE_OUTPUT_SCHEMA,
    EVOCATION_PROMPT, EVOCATION_OUTPUT_SCHEMA,
    COMMITMENT_PROMPT, COMMITMENT_OUTPUT_SCHEMA,
    _CR_STATES, _AMB_STATES, _SE_STATES, _CT_STATES, _ST_STATES,
)


def _compute_mi_intervention_need(
    ambivalence: float,
    change_readiness: float,
    self_efficacy: float,
    change_talk: float,
    sustain_talk: float,
) -> float:
    """后端公式计算 MI 干预需求 —— 不由 LLM 生成。

    矛盾心理（ambivalence）是最强信号（权重 0.35）。
    权重设计：
    - 0.35 × 矛盾（MI 的核心目标）
    - 0.25 × (1 - 准备度)（准备度低 = 需求高）
    - 0.20 × (1 - 自我效能)（效能低 = 需求高）
    - 0.10 × change talk（已有改变话语 = 强化需求）
    - 0.10 × sustain talk（维持现状话语 = 也有需求）
    """
    return round(
        0.35 * ambivalence
        + 0.25 * (1 - change_readiness)
        + 0.20 * (1 - self_efficacy)
        + 0.10 * change_talk
        + 0.10 * sustain_talk,
        3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 技能：mi_state_assessment
# ═══════════════════════════════════════════════════════════════════════════════

class MiStateAssessment(LLMSkill):
    """MI 5 维状态评估 —— 由 LLM 输出 5 个索引，再由后端公式算出干预需求。"""
    name = "mi_state_assessment"
    skill_type = SkillType.ANALYSIS
    description = "LLM-driven MI state assessment across 5 dimensions with confidence and recommendations"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history", [])
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        prev_mi_state = inputs.get("current_mi_state")
        history_str = format_history(history[-6:])
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        prev_str = json.dumps(prev_mi_state, ensure_ascii=False) if prev_mi_state else "no previous assessment"

        return f"""{ASSESSMENT_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

Emotional state (from Emotion Agent):
{emotion_str}

User profile summary:
{profile_str}

Previous MI assessment:
{prev_str}

## Current User Message

{user_text}

## Output JSON

{ASSESSMENT_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        # 提取 5 维索引并夹紧到 [0, 1]
        cr_idx = max(0.0, min(1.0, float(data.get("change_readiness", {}).get("index", 0.0))))
        amb_idx = max(0.0, min(1.0, float(data.get("ambivalence", {}).get("index", 0.0))))
        se_idx = max(0.0, min(1.0, float(data.get("self_efficacy", {}).get("index", 0.0))))
        ct_idx = max(0.0, min(1.0, float(data.get("change_talk", {}).get("index", 0.0))))
        st_idx = max(0.0, min(1.0, float(data.get("sustain_talk", {}).get("index", 0.0))))

        # 后端公式（不由 LLM 决定）
        priority = _compute_mi_intervention_need(amb_idx, cr_idx, se_idx, ct_idx, st_idx)

        # 校验 LLM 给出的 recommended_processes.process 值在 MIProcess 枚举内
        valid_processes = {p.value for p in MIProcess}
        raw_processes = data.get("recommended_processes", [])
        sanitized_processes = []
        for rp in raw_processes:
            proc = rp.get("process", "")
            if proc in valid_processes:
                sanitized_processes.append({
                    "process": proc,
                    "priority": int(rp.get("priority", 99)),
                    "rationale": rp.get("rationale", ""),
                })
        sanitized_processes.sort(key=lambda x: x["priority"])

        # should_intervene：多条件触发（不是只看 priority）
        should_intervene = (
            priority > 0.30
            or amb_idx >= 0.50
            or (discrete_state(cr_idx, _CR_STATES, inclusive=True) in ("contemplation",) and st_idx >= 0.40)
            or (se_idx < 0.40 and ct_idx >= 0.30)
        )

        output = {
            "change_readiness": {
                "index": round(cr_idx, 3),
                "state": discrete_state(cr_idx, _CR_STATES, inclusive=True),
                "confidence": round(float(data.get("change_readiness", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("change_readiness", {}).get("evidence", []),
            },
            "ambivalence": {
                "index": round(amb_idx, 3),
                "state": discrete_state(amb_idx, _AMB_STATES, inclusive=True),
                "confidence": round(float(data.get("ambivalence", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("ambivalence", {}).get("evidence", []),
            },
            "self_efficacy": {
                "index": round(se_idx, 3),
                "state": discrete_state(se_idx, _SE_STATES, inclusive=True),
                "confidence": round(float(data.get("self_efficacy", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("self_efficacy", {}).get("evidence", []),
            },
            "change_talk": {
                "index": round(ct_idx, 3),
                "state": discrete_state(ct_idx, _CT_STATES, inclusive=True),
                "confidence": round(float(data.get("change_talk", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("change_talk", {}).get("evidence", []),
            },
            "sustain_talk": {
                "index": round(st_idx, 3),
                "state": discrete_state(st_idx, _ST_STATES, inclusive=True),
                "confidence": round(float(data.get("sustain_talk", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("sustain_talk", {}).get("evidence", []),
            },
            "mi_intervention_need": priority,
            "overall_trend": data.get("overall_trend", "stable"),
            "primary_concern": data.get("primary_concern", ""),
            "should_intervene": should_intervene,
            "recommended_processes": sanitized_processes,
        }

        return SkillResult(
            skill_name=self.name,
            output=output,
            state_effects={"session.mi_state": "write"},
            success=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION 技能（共享 MiIntervention 基类）
# ═══════════════════════════════════════════════════════════════════════════════

class MiIntervention(TherapyInterventionSkill):
    """MI INTERVENTION 技能的家族基类。"""

    state_effects_value = {"session.mi_intervention_log": "append"}
    interaction_style = InteractionStyle.EXPLORATORY.value

class MiExplore(MiIntervention):
    """mi_explore：通过 OARS / 议题映射建立关系、探索矛盾。"""
    name = "mi_explore"
    skill_type = SkillType.INTERVENTION
    description = "Build engagement: OARS for relationship building, agenda_mapping for focusing"

    prompt_template = EXPLORE_PROMPT
    output_schema = EXPLORE_OUTPUT_SCHEMA
    default_technique = MIExploreTechnique.OARS.value
    valid_techniques = [t.value for t in MIExploreTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 探索阶段聚焦"矛盾度"和"维持现状话语" —— 越矛盾越需要 OARS，
        # 维持话语越多越需要温和回应。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        amb_info = json.dumps(assessment.get("ambivalence", {}), ensure_ascii=False)
        st_info = json.dumps(assessment.get("sustain_talk", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Ambivalence: {amb_info}
Sustain talk: {st_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 矛盾中/强 → OARS（建立关系 + 探索）；否则 → agenda_mapping（聚焦）。
        assessment = inputs.get("assessment", {})
        amb_state = assessment.get("ambivalence", {}).get("state", "moderate")
        if amb_state in ("moderate", "strong"):
            tech = MIExploreTechnique.OARS
            goal = "通过开放式提问和反思性倾听，帮助用户探索内心的矛盾，不做评判"
        else:
            tech = MIExploreTechnique.AGENDA_MAPPING
            goal = "帮助用户梳理当前关心的议题，让用户自己决定从哪里开始"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Build engagement and explore the user's ambivalence without pushing for change",
                    "steps": [
                        "用开放式提问邀请用户分享更多",
                        "通过反思性倾听回应，让用户感到被理解",
                        "温和地邀请用户觉察自己内心不同的声音",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "温和、好奇、不评判",
                    "pace_adjustment": "slow",
                    "culture_note": "用'你自己觉得呢？'替代'你应该'；肯定要具体而非笼统；矛盾是正常的不是抗拒",
                },
                "contraindications": [
                    {"condition": "crisis", "reason": "危机状态下不适合探索矛盾，优先确保安全"}
                ],
            },
            state_effects={"session.mi_intervention_log": "append"},
            success=True,
        )


class MiEvocation(MiIntervention):
    """mi_evocation：引出并强化用户的 change talk（DARN-CAT / 量尺）。"""
    name = "mi_evocation"
    skill_type = SkillType.INTERVENTION
    description = "Elicit change talk: DARN-CAT exploration or Importance/Confidence Ruler"

    prompt_template = EVOCATION_PROMPT
    output_schema = EVOCATION_OUTPUT_SCHEMA
    default_technique = MIEvocationTechnique.DARN_CAT.value
    valid_techniques = [t.value for t in MIEvocationTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 引出阶段聚焦"change talk"和"自我效能"—— 越弱越需要用 DARN-CAT
        # 把理由一点点挖出来。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        ct_info = json.dumps(assessment.get("change_talk", {}), ensure_ascii=False)
        se_info = json.dumps(assessment.get("self_efficacy", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Change talk: {ct_info}
Self-efficacy: {se_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # change talk 缺失/弱 → DARN-CAT 框架挖理由；否则 → 量尺评分量化动力。
        assessment = inputs.get("assessment", {})
        ct_state = assessment.get("change_talk", {}).get("state", "absent")

        if ct_state in ("absent", "weak"):
            tech = MIEvocationTechnique.DARN_CAT
            goal = "通过DARN-CAT框架帮助用户发现和表达自己想改变的理由"
        else:
            tech = MIEvocationTechnique.IMPORTANCE_CONFIDENCE_RULER
            goal = "用量尺评分帮助用户量化改变的决心和能力感，强化已有的改变意愿"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Elicit and strengthen the user's own reasons for change",
                    "steps": [
                        "邀请用户分享改变对自己意味着什么",
                        "用评分或探索性问题引出用户的改变语句",
                        "反射和强化用户说出的改变理由",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "好奇、温和地引导，不打鸡血不施压",
                    "pace_adjustment": "normal",
                    "culture_note": "用'你自己想改变的原因是什么'替代'你应该改变'；'为什么不是0分'比'为什么不是10分'更能引出改变理由",
                },
                "contraindications": [
                    {"condition": "no_change_talk_present", "reason": "用户完全没有表达改变意愿时，先回到 mi_explore 建立关系"},
                    {"condition": "crisis", "reason": "危机状态下不适合做动机探索"},
                ],
            },
            state_effects={"session.mi_intervention_log": "append"},
            success=True,
        )


class MiCommitment(MiIntervention):
    """mi_commitment：把 change talk 固化为具体的改变计划。"""
    name = "mi_commitment"
    skill_type = SkillType.INTERVENTION
    description = "Consolidate commitment: change plan formulation or commitment language strengthening"

    prompt_template = COMMITMENT_PROMPT
    output_schema = COMMITMENT_OUTPUT_SCHEMA
    default_technique = MICommitmentTechnique.CHANGE_PLAN.value
    valid_techniques = [t.value for t in MICommitmentTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 承诺阶段同时看"准备度"、"change talk"和"自我效能"—— 三者越充分
        # 越适合做具体计划，否则先巩固承诺语句。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        cr_info = json.dumps(assessment.get("change_readiness", {}), ensure_ascii=False)
        ct_info = json.dumps(assessment.get("change_talk", {}), ensure_ascii=False)
        se_info = json.dumps(assessment.get("self_efficacy", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Change readiness: {cr_info}
Change talk: {ct_info}
Self-efficacy: {se_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 准备度已进入 preparation/action → change_plan；否则 → commitment_language。
        assessment = inputs.get("assessment", {})
        cr_state = assessment.get("change_readiness", {}).get("state", "precontemplation")
        if cr_state in ("preparation", "action"):
            tech = MICommitmentTechnique.CHANGE_PLAN
            goal = "帮助用户制定具体、可操作的改变计划，由用户自己主导"
        else:
            tech = MICommitmentTechnique.COMMITMENT_LANGUAGE
            goal = "帮助用户巩固和强化已经表达的承诺语句，让承诺变得更具体"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user consolidate commitment into an actionable plan",
                    "steps": [
                        "回顾用户已经表达的想要改变的意愿",
                        "邀请用户把意愿转化为具体的行动计划",
                        "确认用户对计划的承诺和支持系统",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "鼓励、相信用户、不施压",
                    "pace_adjustment": "normal",
                    "culture_note": "由用户主导制定计划；尊重用户的实际节奏；问'谁可以支持你'强调支持系统",
                },
                "contraindications": [
                    {"condition": "readiness_too_low", "reason": "用户改变准备度不足时，commitment 会被体验为施压，应先回到 mi_explore"},
                    {"condition": "crisis", "reason": "危机状态下不适合做改变计划"},
                ],
            },
            state_effects={"session.mi_intervention_log": "append"},
            success=True,
        )


# ── 注册到全局 skill_registry ─────────────────────────────────────
skill_registry.register(MiStateAssessment())
skill_registry.register(MiExplore())
skill_registry.register(MiEvocation())
skill_registry.register(MiCommitment())
