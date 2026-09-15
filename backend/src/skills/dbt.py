"""DBT（辩证行为疗法）技能实现。

5 个技能：
- 1 个 ANALYSIS（LLM 驱动）：dbt_state_assessment —— 5 维评估，
  其中 `dbt_intervention_priority` 由后端公式计算（不由 LLM 生成）
- 4 个 INTERVENTION（LLM 驱动）：dbt_mindfulness、dbt_distress_tolerance、
  dbt_emotion_regulation、dbt_interpersonal_effectiveness

LLM 用的 prompt / output schema / 状态阈值在 `src.skills.prompts.dbt`，
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
    DBTProcess,
    DistressToleranceTechnique,
    EmotionRegulationTechnique,
    InterpersonalEffectivenessTechnique,
    MindfulnessTechnique,
)
from src.skills.prompts.dbt import (
    ASSESSMENT_PROMPT, ASSESSMENT_OUTPUT_SCHEMA,
    MINDFULNESS_PROMPT, MINDFULNESS_OUTPUT_SCHEMA,
    DISTRESS_TOLERANCE_PROMPT, DISTRESS_TOLERANCE_OUTPUT_SCHEMA,
    EMOTION_REGULATION_PROMPT, EMOTION_REGULATION_OUTPUT_SCHEMA,
    INTERPERSONAL_PROMPT, INTERPERSONAL_OUTPUT_SCHEMA,
    _ED_STATES, _DL_STATES, _IMP_STATES, _IC_STATES, _MA_STATES,
)


def _compute_dbt_intervention_priority(
    emotion_dysregulation: float,
    distress_level: float,
    impulsivity: float,
    interpersonal_conflict: float,
    mindfulness_awareness: float,
) -> float:
    """后端公式计算 DBT 干预优先级 —— 不由 LLM 生成。

    权重设计：
    - 0.25 × 情绪失调（需要调节）
    - 0.25 × 痛苦水平（需要耐受）
    - 0.15 × 冲动性（需要耐受 / 正念）
    - 0.15 × 人际冲突（需要人际效能）
    - 0.20 × (1 - 正念觉察)（缺正念 = 缺 DBT 基础）
    """
    return round(
        0.25 * emotion_dysregulation
        + 0.25 * distress_level
        + 0.15 * impulsivity
        + 0.15 * interpersonal_conflict
        + 0.20 * (1 - mindfulness_awareness),
        3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 技能：dbt_state_assessment
# ═══════════════════════════════════════════════════════════════════════════════

class DbtStateAssessment(LLMSkill):
    """DBT 5 维状态评估 —— 由 LLM 输出 5 个索引，再由后端公式算出优先级。"""
    name = "dbt_state_assessment"
    skill_type = SkillType.ANALYSIS
    description = "LLM-driven DBT state assessment across 5 dimensions with confidence and recommendations"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history", [])
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        prev_dbt_state = inputs.get("current_dbt_state")
        history_str = format_history(history[-6:])
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        prev_str = json.dumps(prev_dbt_state, ensure_ascii=False) if prev_dbt_state else "no previous assessment"

        return f"""{ASSESSMENT_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

Emotional state (from Emotion Agent):
{emotion_str}

User profile summary:
{profile_str}

Previous DBT assessment:
{prev_str}

## Current User Message

{user_text}

## Output JSON

{ASSESSMENT_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        # 提取 5 维索引并夹紧到 [0, 1]
        ed_idx = max(0.0, min(1.0, float(data.get("emotion_dysregulation", {}).get("index", 0.0))))
        dl_idx = max(0.0, min(1.0, float(data.get("distress_level", {}).get("index", 0.0))))
        imp_idx = max(0.0, min(1.0, float(data.get("impulsivity", {}).get("index", 0.0))))
        ic_idx = max(0.0, min(1.0, float(data.get("interpersonal_conflict", {}).get("index", 0.0))))
        ma_idx = max(0.0, min(1.0, float(data.get("mindfulness_awareness", {}).get("index", 0.5))))

        # 后端公式（不由 LLM 决定）
        priority = _compute_dbt_intervention_priority(ed_idx, dl_idx, imp_idx, ic_idx, ma_idx)

        # 校验 LLM 给出的 recommended_processes.process 值在 DBTProcess 枚举内
        valid_processes = {p.value for p in DBTProcess}
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

        output = {
            "emotion_dysregulation": {
                "index": round(ed_idx, 3),
                "state": discrete_state(ed_idx, _ED_STATES),
                "confidence": round(float(data.get("emotion_dysregulation", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("emotion_dysregulation", {}).get("evidence", []),
            },
            "distress_level": {
                "index": round(dl_idx, 3),
                "state": discrete_state(dl_idx, _DL_STATES),
                "confidence": round(float(data.get("distress_level", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("distress_level", {}).get("evidence", []),
            },
            "impulsivity": {
                "index": round(imp_idx, 3),
                "state": discrete_state(imp_idx, _IMP_STATES),
                "confidence": round(float(data.get("impulsivity", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("impulsivity", {}).get("evidence", []),
            },
            "interpersonal_conflict": {
                "index": round(ic_idx, 3),
                "state": discrete_state(ic_idx, _IC_STATES),
                "confidence": round(float(data.get("interpersonal_conflict", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("interpersonal_conflict", {}).get("evidence", []),
            },
            "mindfulness_awareness": {
                "index": round(ma_idx, 3),
                "state": discrete_state(ma_idx, _MA_STATES),
                "confidence": round(float(data.get("mindfulness_awareness", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("mindfulness_awareness", {}).get("evidence", []),
            },
            "dbt_intervention_priority": priority,
            "overall_trend": data.get("overall_trend", "stable"),
            "primary_concern": data.get("primary_concern", ""),
            "should_intervene": bool(data.get("should_intervene", False)),
            "recommended_processes": sanitized_processes,
        }

        return SkillResult(
            skill_name=self.name,
            output=output,
            state_effects={"session.dbt_state": "write"},
            success=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION 技能（共享 DbtIntervention 基类）
# ═══════════════════════════════════════════════════════════════════════════════

class DbtIntervention(TherapyInterventionSkill):
    """DBT INTERVENTION 技能的家族基类。"""

    state_effects_value = {"session.dbt_intervention_log": "append"}
    interaction_style = InteractionStyle.EXPLORATORY.value

class DbtMindfulness(DbtIntervention):
    """dbt_mindfulness：建立当下觉察（智慧心念 + 观察描述）。"""
    name = "dbt_mindfulness"
    skill_type = SkillType.INTERVENTION
    description = "Build core mindfulness: Wise Mind integration and observe-describe awareness"

    prompt_template = MINDFULNESS_PROMPT
    output_schema = MINDFULNESS_OUTPUT_SCHEMA
    default_technique = MindfulnessTechnique.WISE_MIND.value
    valid_techniques = [t.value for t in MindfulnessTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 正念技能同时看"正念觉察"和"情绪失调"两个维度。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        ma_info = json.dumps(assessment.get("mindfulness_awareness", {}), ensure_ascii=False)
        ed_info = json.dumps(assessment.get("emotion_dysregulation", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Mindfulness awareness: {ma_info}
Emotion dysregulation: {ed_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 正念觉察低 → observe_describe（先停下来观察）；否则 → wise_mind（整合）。
        assessment = inputs.get("assessment", {})
        ma_state = assessment.get("mindfulness_awareness", {}).get("state", "moderate")
        if ma_state in ("absent", "low"):
            tech = MindfulnessTechnique.OBSERVE_DESCRIBE
            goal = "帮助用户停下来观察内在体验，用不评判的语言描述当下的感受和想法"
        else:
            tech = MindfulnessTechnique.WISE_MIND
            goal = "帮助用户整合理性思考和情绪感受，找到智慧心念的答案"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Guide user toward mindful awareness as the foundation for further work",
                    "steps": [
                        "邀请用户暂停一下，留意当下的感受",
                        "帮助用户区分 '理性在说什么' 和 '情绪在说什么'",
                        "引导用户寻找两者的交汇点 — 智慧心念的答案",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "温和、不催促",
                    "pace_adjustment": "slow",
                    "culture_note": "用'停下来看看'、'心里其实知道答案'替代正念/冥想等术语",
                },
                "contraindications": [
                    {"condition": "crisis", "reason": "危机状态下不适合做正念教学，优先 distress_tolerance"}
                ],
            },
            state_effects={"session.dbt_intervention_log": "append"},
            success=True,
        )


class DbtDistressTolerance(DbtIntervention):
    """dbt_distress_tolerance：在痛苦时刻存活下来，不把它搞得更糟。"""
    name = "dbt_distress_tolerance"
    skill_type = SkillType.INTERVENTION
    description = "Help user endure pain and crisis skillfully — radical acceptance, self-soothe"

    prompt_template = DISTRESS_TOLERANCE_PROMPT
    output_schema = DISTRESS_TOLERANCE_OUTPUT_SCHEMA
    default_technique = DistressToleranceTechnique.RADICAL_ACCEPTANCE.value
    valid_techniques = [t.value for t in DistressToleranceTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 痛苦耐受聚焦"痛苦水平"和"冲动性"—— 两者都高时优先五感安抚。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        dl_info = json.dumps(assessment.get("distress_level", {}), ensure_ascii=False)
        imp_info = json.dumps(assessment.get("impulsivity", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Distress level: {dl_info}
Impulsivity: {imp_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 痛苦/冲动高 → self_soothe（先安抚）；否则 → radical_acceptance（停止与现实对抗）。
        assessment = inputs.get("assessment", {})
        dl_state = assessment.get("distress_level", {}).get("state", "moderate")
        imp_state = assessment.get("impulsivity", {}).get("state", "controlled")

        if dl_state in ("high", "crisis") or imp_state in ("high", "urgent"):
            tech = DistressToleranceTechnique.SELF_SOOTHE
            goal = "帮助用户在痛苦时刻通过五感安抚自己，先度过这一刻，不急着解决问题"
        else:
            tech = DistressToleranceTechnique.RADICAL_ACCEPTANCE
            goal = "帮助用户接纳已经发生的现实，停止与现实抗争，减少痛苦感的叠加"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user survive the crisis moment without making it worse",
                    "steps": [
                        "确认用户当下的痛苦是真实的，不急于解决",
                        "引导用户区分 '已经发生的事' 和 '我对这件事的反应'",
                        "温和邀请用户尝试：承认这件事已经发生了，停一停对抗",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "温暖、沉稳、不催促",
                    "pace_adjustment": "slow",
                    "culture_note": "DBT接纳=接纳已经发生的外部现实（区别于ACT接纳内部体验）；用'事情已经这样了，不再对抗'替代单纯说'接纳'",
                },
                "contraindications": [
                    {"condition": "radical_acceptance_too_soon", "reason": "用户尚未被充分倾听时，radical acceptance 可能被体验为 dismissive"}
                ],
            },
            state_effects={"session.dbt_intervention_log": "append"},
            success=True,
        )


class DbtEmotionRegulation(DbtIntervention):
    """dbt_emotion_regulation：理解并调节情绪反应。"""
    name = "dbt_emotion_regulation"
    skill_type = SkillType.INTERVENTION
    description = "Reduce emotional vulnerability: check the facts, use opposite action when needed"

    prompt_template = EMOTION_REGULATION_PROMPT
    output_schema = EMOTION_REGULATION_OUTPUT_SCHEMA
    default_technique = EmotionRegulationTechnique.CHECK_THE_FACTS.value
    valid_techniques = [t.value for t in EmotionRegulationTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 情绪调节同时看"失调度"和"正念觉察"—— 正念越高越适合 check_the_facts。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        ed_info = json.dumps(assessment.get("emotion_dysregulation", {}), ensure_ascii=False)
        ma_info = json.dumps(assessment.get("mindfulness_awareness", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Emotion dysregulation: {ed_info}
Mindfulness awareness: {ma_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 失调极端 → check_the_facts（先看事实是否支持这么强情绪）；否则 → opposite_action。
        assessment = inputs.get("assessment", {})
        ed_state = assessment.get("emotion_dysregulation", {}).get("state", "moderate")
        if ed_state in ("severe", "extreme"):
            tech = EmotionRegulationTechnique.CHECK_THE_FACTS
            goal = "帮助用户核查事实：当前的情绪反应强度是否真的与事实匹配"
        else:
            tech = EmotionRegulationTechnique.OPPOSITE_ACTION
            goal = "帮助用户识别情绪驱动的冲动行为，尝试相反的行为来打破情绪循环"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user regulate emotional responses through fact-checking or opposite action",
                    "steps": [
                        "帮助用户识别当前的情绪和它驱动的冲动行为",
                        "引导用户核查：事实是否真的支持这么强烈的情绪反应？",
                        "如果不匹配，温和邀请用户尝试相反的行为",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "好奇探索而非纠正，绝不否定用户的情绪",
                    "pace_adjustment": "normal",
                    "culture_note": "用'看看事实是不是真的支持这种感觉'替代'你的情绪不合理'；'试试反过来做'替代'你不应该这样做'",
                },
                "contraindications": [
                    {"condition": "emotion_is_justified", "reason": "情绪反应与事实匹配时，check_the_facts 可能被体验为 gaslighting，应改用 distress_tolerance"},
                    {"condition": "crisis_level_distress", "reason": "痛苦水平过高时，先做 distress_tolerance，不要直接跳到情绪调节"},
                ],
            },
            state_effects={"session.dbt_intervention_log": "append"},
            success=True,
        )


class DbtInterpersonalEffectiveness(DbtIntervention):
    """dbt_interpersonal_effectiveness：平衡目标、关系、自尊的沟通技能。"""
    name = "dbt_interpersonal_effectiveness"
    skill_type = SkillType.INTERVENTION
    description = "Balance objectives, relationships, and self-respect: DEAR MAN, GIVE"

    prompt_template = INTERPERSONAL_PROMPT
    output_schema = INTERPERSONAL_OUTPUT_SCHEMA
    default_technique = InterpersonalEffectivenessTechnique.DEAR_MAN.value
    valid_techniques = [t.value for t in InterpersonalEffectivenessTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 人际效能聚焦"人际冲突"和"情绪失调"—— 冲突升级时需要优先 GIVE。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        ic_info = json.dumps(assessment.get("interpersonal_conflict", {}), ensure_ascii=False)
        ed_info = json.dumps(assessment.get("emotion_dysregulation", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Interpersonal conflict: {ic_info}
Emotion dysregulation: {ed_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 冲突高/严重 → GIVE（优先保关系）；否则 → DEAR_MAN（清晰表达需求）。
        assessment = inputs.get("assessment", {})
        ic_state = assessment.get("interpersonal_conflict", {}).get("state", "moderate")
        if ic_state in ("high", "severe"):
            tech = InterpersonalEffectivenessTechnique.GIVE
            goal = "帮助用户优先维护关系——用温和、关心、认可和轻松的方式来沟通"
        else:
            tech = InterpersonalEffectivenessTechnique.DEAR_MAN
            goal = "帮助用户清晰表达自己的需求和边界，同时保持关系的平衡"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user communicate effectively — balancing goals, relationships, and self-respect",
                    "steps": [
                        "帮助用户理清这次沟通的优先目标",
                        "引导用户用清晰、自信但不攻击的方式表达",
                        "确认用户对可能的结果有心理准备",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "务实、鼓励、不施压",
                    "pace_adjustment": "normal",
                    "culture_note": "用'清楚地表达自己的需求'替代'assertiveness'；GIVE 与中式关系价值观天然契合，强调这是已有的优势只需更刻意地使用",
                },
                "contraindications": [
                    {"condition": "safety_risk", "reason": "如果关系中存在虐待或安全风险，DEAR MAN 可能不是合适的策略"},
                    {"condition": "high_emotion_dysregulation", "reason": "情绪严重失调时，先做 emotion_regulation 或 distress_tolerance"},
                ],
            },
            state_effects={"session.dbt_intervention_log": "append"},
            success=True,
        )


# ── 注册到全局 skill_registry ─────────────────────────────────────
skill_registry.register(DbtStateAssessment())
skill_registry.register(DbtMindfulness())
skill_registry.register(DbtDistressTolerance())
skill_registry.register(DbtEmotionRegulation())
skill_registry.register(DbtInterpersonalEffectiveness())
