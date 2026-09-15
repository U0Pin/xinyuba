"""ACT（接纳承诺疗法）技能实现。

7 个技能：
- 1 个 ANALYSIS（LLM 驱动）：act_state_assessment —— 5 维心理灵活性评估，
  其中 psychological_flexibility 由后端公式计算（不由 LLM 生成）
- 6 个 INTERVENTION（LLM 驱动）：act_defusion、act_acceptance、
  act_present_moment、act_self_as_context、act_values、act_committed_action

LLM 用的 prompt / output schema 在 `src.skills.prompts.act`，
本文件只展示 skill 类的骨架与注册逻辑。
"""

import json

from src.core.act_state import (
    ACTStateVector, InteractionStyle,
)

from src.core.skill import (
    LLMSkill,
    SkillResult,
    SkillType,
    TherapyInterventionSkill,
    format_history,
    skill_registry,
)
from src.skills.techniques import (
    DefusionTechnique, AcceptanceTechnique, PresentMomentTechnique,
    SelfAsContextTechnique, ValuesTechnique, CommittedActionTechnique,
)
from src.skills.prompts.act import (
    ASSESSMENT_PROMPT, ASSESSMENT_OUTPUT_SCHEMA,
    DEFUSION_PROMPT, DEFUSION_OUTPUT_SCHEMA,
    ACCEPTANCE_PROMPT, ACCEPTANCE_OUTPUT_SCHEMA,
    PRESENT_MOMENT_PROMPT, PRESENT_MOMENT_OUTPUT_SCHEMA,
    SELF_AS_CONTEXT_PROMPT, SELF_AS_CONTEXT_OUTPUT_SCHEMA,
    VALUES_PROMPT, VALUES_OUTPUT_SCHEMA,
    COMMITTED_ACTION_PROMPT, COMMITTED_ACTION_OUTPUT_SCHEMA,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 技能：act_state_assessment
# ═══════════════════════════════════════════════════════════════════════════════

class ActStateAssessment(LLMSkill):
    """ACT 5 维状态评估 —— 由 LLM 输出 5 个索引，再由后端公式算出灵活性。

    注意：`emotional_intensity` 的提取与 prompt 输出的字段名不完全匹配
    （已知遗留问题，见 REFACTORING_HANDOVER.md 第四节第 3 条），保持原样。
    """
    name = "act_state_assessment"
    skill_type = SkillType.ANALYSIS
    description = "LLM-driven ACT state assessment across 5 dimensions with confidence and recommendations"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history", [])
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        prev_act_state = inputs.get("current_act_state")
        # 拼接 prompt 上下文块
        history_str = format_history(history[-6:])
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        prev_str = json.dumps(prev_act_state, ensure_ascii=False) if prev_act_state else "no previous assessment"

        return f"""{ASSESSMENT_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

Emotional state (from Emotion Agent):
{emotion_str}

User profile summary:
{profile_str}

Previous ACT assessment:
{prev_str}

## Current User Message

{user_text}

## Output JSON

{ASSESSMENT_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        # 提取 LLM 给出的 5 维索引并夹紧到 [0, 1]
        emotion = inputs.get("emotion", {})
        fusion_idx = float(data.get("fusion", {}).get("index", 0.0))
        avoidance_idx = float(data.get("avoidance", {}).get("index", 0.0))
        openness_idx = float(data.get("openness", {}).get("index", 0.5))
        alignment_idx = float(data.get("alignment", {}).get("index", 0.5))
        activation_idx = float(data.get("activation", {}).get("index", 0.5))

        fusion_idx = max(0.0, min(1.0, fusion_idx))
        avoidance_idx = max(0.0, min(1.0, avoidance_idx))
        openness_idx = max(0.0, min(1.0, openness_idx))
        alignment_idx = max(0.0, min(1.0, alignment_idx))
        activation_idx = max(0.0, min(1.0, activation_idx))

        # 后端公式（不由 LLM 决定）
        pf = ACTStateVector.compute_flexibility(
            fusion_idx, avoidance_idx, openness_idx, alignment_idx, activation_idx
        )

        # 情绪强度来自 PAD arousal（Phase 3 修复：读扁平 key，与编排传入的
        # emotion 结构一致；旧实现读 emotion["pad"]["A"] 恒为 0）
        arousal = float(emotion.get("arousal", 0.0))
        emotional_intensity = round(max(0.0, arousal), 3)

        escalation_level = ACTStateVector.compute_escalation(emotional_intensity)
        fusion_state = ACTStateVector.compute_fusion_state(fusion_idx)
        avoidance_state = ACTStateVector.compute_avoidance_state(avoidance_idx)
        openness_state = ACTStateVector.compute_openness_state(openness_idx)
        flexibility_level = ACTStateVector.compute_flexibility_level(pf)

        output = {
            "fusion": {
                "index": round(fusion_idx, 3),
                "state": fusion_state.value,
                "confidence": round(float(data.get("fusion", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("fusion", {}).get("evidence", []),
            },
            "avoidance": {
                "index": round(avoidance_idx, 3),
                "state": avoidance_state.value,
                "confidence": round(float(data.get("avoidance", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("avoidance", {}).get("evidence", []),
            },
            "openness": {
                "index": round(openness_idx, 3),
                "state": openness_state.value,
                "confidence": round(float(data.get("openness", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("openness", {}).get("evidence", []),
            },
            "alignment": {
                "index": round(alignment_idx, 3),
                "state": "low" if alignment_idx < 0.33 else ("medium" if alignment_idx < 0.66 else "high"),
                "confidence": round(float(data.get("alignment", {}).get("confidence", 0.5)), 3),
                "values_mentioned": data.get("alignment", {}).get("values_mentioned", []),
            },
            "activation": {
                "index": round(activation_idx, 3),
                "state": "low" if activation_idx < 0.33 else ("medium" if activation_idx < 0.66 else "high"),
                "confidence": round(float(data.get("activation", {}).get("confidence", 0.5)), 3),
            },
            "psychological_flexibility": pf,
            "flexibility_level": flexibility_level.value,
            "overall_trend": data.get("overall_trend", "stable"),
            "emotional_intensity": emotional_intensity,
            "escalation_level": escalation_level.name.lower(),
            "primary_concern": data.get("primary_concern", ""),
            "should_intervene": bool(data.get("should_intervene", False)),
            "recommended_processes": data.get("recommended_processes", []),
        }

        return SkillResult(
            skill_name=self.name,
            output=output,
            state_effects={"session.act_state": "write"},
            success=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION 技能（共享 ActIntervention 基类）
# ═══════════════════════════════════════════════════════════════════════════════

class ActIntervention(TherapyInterventionSkill):
    """ACT INTERVENTION 技能的家族基类：统一的 state_effects 与对话风格。"""

    state_effects_value = {"session.act_intervention_log": "append"}
    interaction_style = InteractionStyle.EXPLORATORY.value

class ActDefusion(ActIntervention):
    """act_defusion：帮用户和想法拉开距离（认知解离）。"""
    name = "act_defusion"
    skill_type = SkillType.INTERVENTION
    description = "Help user see thoughts as mental events, not identity or literal truth"

    prompt_template = DEFUSION_PROMPT
    output_schema = DEFUSION_OUTPUT_SCHEMA
    default_technique = DefusionTechnique.THOUGHT_LABELING.value
    valid_techniques = [t.value for t in DefusionTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 解离聚焦"融合度"，让 LLM 看到 fusion 评估结果再选技术。
        # 注意：`user_text` / `target_thought` / `history` 是基类统一签名，
        # 但本技能不读取 —— 仅从 assessment / emotion / profile 中取信息。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        fusion_info = json.dumps(assessment.get("fusion", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Fusion assessment: {fusion_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败时的兜底：保守用 thought_labeling。"""
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": DefusionTechnique.THOUGHT_LABELING.value,
                    "description": "Help user notice thoughts as mental events",
                    "steps": ["Invite the user to notice the thought", "Help them label it as a thought"],
                },
                "conversation_goal": "帮助用户注意到当下的想法只是一个想法，不是事实",
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "好奇而非纠正",
                    "pace_adjustment": "slow",
                    "culture_note": "用好奇而非辩论的态度面对宿命感表达",
                },
                "contraindications": [
                    {"condition": "identity_fusion", "reason": "身份融合时直接解离可能被体验为否定"}
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


class ActAcceptance(ActIntervention):
    """act_acceptance：邀请用户允许情绪存在，减少体验性回避。"""
    name = "act_acceptance"
    skill_type = SkillType.INTERVENTION
    description = "Invite user to allow emotions to be present, reducing experiential avoidance"

    prompt_template = ACCEPTANCE_PROMPT
    output_schema = ACCEPTANCE_OUTPUT_SCHEMA
    default_technique = AcceptanceTechnique.WILLINGNESS_INVITATION.value
    valid_techniques = [t.value for t in AcceptanceTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 接纳聚焦"回避度"。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        avoidance_info = json.dumps(assessment.get("avoidance", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Avoidance assessment: {avoidance_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": AcceptanceTechnique.WILLINGNESS_INVITATION.value,
                    "description": "Invite user to notice and allow the present emotion",
                    "steps": ["Notice what you're feeling right now", "See if you can allow it to be there, just for a moment"],
                },
                "conversation_goal": "帮助用户允许当下的情绪存在，不需要推开它",
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "温暖且坚定",
                    "pace_adjustment": "slow",
                    "culture_note": "尊重用户原有的应对方式，温和邀请停留片刻",
                },
                "contraindications": [
                    {"condition": "high_escalation", "reason": "情绪强度过高时接纳可能导致淹没感"}
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


class ActPresentMoment(ActIntervention):
    """act_present_moment：把用户从反刍/未来焦虑里拉回当下感官体验。"""
    name = "act_present_moment"
    skill_type = SkillType.INTERVENTION
    description = "Guide user back to present-moment sensory experience, away from cognitive fusion"

    prompt_template = PRESENT_MOMENT_PROMPT
    output_schema = PRESENT_MOMENT_OUTPUT_SCHEMA
    default_technique = PresentMomentTechnique.BREATH_ANCHOR.value
    valid_techniques = [t.value for t in PresentMomentTechnique]
    interaction_style = InteractionStyle.ANCHORING.value

    def _context_block(self, inputs: dict) -> str:
        # 当下觉察聚焦"开放度"—— 开放度越低越需要强锚定。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        openness_info = json.dumps(assessment.get("openness", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Openness assessment: {openness_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 根据开放度档位选技术：越关闭越要用最小压力的技术。
        assessment = inputs.get("assessment", {})
        openness_state = assessment.get("openness", {}).get("state", "eo_guarded")
        if "shutdown" in openness_state:
            tech = PresentMomentTechnique.DROPPING_ANCHOR
            steps = ["Name one thing you can see right now", "Name one thing you can feel touching you"]
        elif "defended" in openness_state:
            tech = PresentMomentTechnique.FIVE_SENSES
            steps = ["Notice 3 things you can see", "Notice 2 things you can feel"]
        else:
            tech = PresentMomentTechnique.BREATH_ANCHOR
            steps = ["Notice your breathing — don't change it, just notice", "Feel where the breath moves in your body"]

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Ground user in present-moment sensory experience",
                    "steps": steps,
                },
                "conversation_goal": "帮助用户从情绪旋涡中暂时抽离，回到此时此地",
                "adaptation": {
                    "interaction_style": InteractionStyle.ANCHORING.value,
                    "tone_adjustment": "平稳、锚定",
                    "pace_adjustment": "slow",
                    "culture_note": "避免禅修/正念等词汇，用'留意一下'替代",
                },
                "contraindications": [
                    {"condition": "shutdown", "reason": "完全关闭时要求感官觉察可能感到被侵犯"}
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


class ActSelfAsContext(ActIntervention):
    """act_self_as_context：通过隐喻让用户接触"观察性自我"。"""
    name = "act_self_as_context"
    skill_type = SkillType.INTERVENTION
    description = "Use metaphor to help user access the observing self, distinct from thoughts and feelings"

    prompt_template = SELF_AS_CONTEXT_PROMPT
    output_schema = SELF_AS_CONTEXT_OUTPUT_SCHEMA
    default_technique = SelfAsContextTechnique.OBSERVER_METAPHOR.value
    valid_techniques = [t.value for t in SelfAsContextTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 观察性自我聚焦"融合度"—— 融合越深越需要隐喻来松动身份。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        fusion_info = json.dumps(assessment.get("fusion", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Fusion assessment: {fusion_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": SelfAsContextTechnique.OBSERVER_METAPHOR.value,
                    "description": "Use observer metaphor to help user access observing self",
                    "steps": [
                        "Introduce the idea that there's a part of us that notices everything",
                        "Invite the user to notice: who is noticing these thoughts right now?",
                        "Give space — don't push for an answer",
                    ],
                },
                "conversation_goal": "帮助用户感受到：即使这些想法存在，还有一个更大的'我'在观察它们",
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "温和、不急切",
                    "pace_adjustment": "very_slow",
                    "culture_note": "优先使用自然意象，用'退一步看'替代'观察性自我'",
                },
                "contraindications": [
                    {"condition": "defended", "reason": "高度防御时抽象隐喻无法到达用户"}
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


class ActValues(ActIntervention):
    """act_values：探索对用户真正重要的东西（价值澄清）。"""
    name = "act_values"
    skill_type = SkillType.INTERVENTION
    description = "Explore what matters to the user — values as life direction, not destination"

    prompt_template = VALUES_PROMPT
    output_schema = VALUES_OUTPUT_SCHEMA
    default_technique = ValuesTechnique.VALUES_EXPLORATION.value
    valid_techniques = [t.value for t in ValuesTechnique]
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _context_block(self, inputs: dict) -> str:
        # 价值澄清聚焦"对齐度"—— 越低越需要探索。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        alignment_info = json.dumps(assessment.get("alignment", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Alignment assessment: {alignment_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": ValuesTechnique.VALUES_EXPLORATION.value,
                    "description": "Explore what matters to the user through open-ended questions",
                    "steps": [
                        "Ask: what about this situation hurts the most?",
                        "Reflect: that pain might point to something you care deeply about",
                    ],
                },
                "conversation_goal": "帮助用户发现：痛苦本身指向了你在乎的东西是什么",
                "adaptation": {
                    "interaction_style": InteractionStyle.EXPLORATORY.value,
                    "tone_adjustment": "好奇、欣赏",
                    "pace_adjustment": "normal",
                    "culture_note": "从关系/成长/责任等高频价值观切入；用'什么对你重要'替代'价值观'",
                },
                "contraindications": [
                    {"condition": "escalating", "reason": "情绪高涨时不适合做抽象价值观讨论"},
                    {"condition": "low_readiness", "reason": "用户尚未准备好认知探索"},
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


class ActCommittedAction(ActIntervention):
    """act_committed_action：把价值方向变成微小的行动承诺。"""
    name = "act_committed_action"
    skill_type = SkillType.INTERVENTION
    description = "Create small, concrete behavioral commitments aligned with personal values"

    prompt_template = COMMITTED_ACTION_PROMPT
    output_schema = COMMITTED_ACTION_OUTPUT_SCHEMA
    default_technique = CommittedActionTechnique.ACTION_PLANNING.value
    valid_techniques = [t.value for t in CommittedActionTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 行动承诺同时看"对齐度"（做什么）和"激活度"（做多少）。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        alignment_info = json.dumps(assessment.get("alignment", {}), ensure_ascii=False)
        activation_info = json.dumps(assessment.get("activation", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Alignment assessment: {alignment_info}
Activation assessment: {activation_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": CommittedActionTechnique.ACTION_PLANNING.value,
                    "description": "Help user create a small, concrete behavioral commitment",
                    "steps": [
                        "Clarify the value direction",
                        "Identify one tiny action (5 min or less)",
                        "Ask: would you be willing to try this?",
                    ],
                },
                "conversation_goal": "帮助用户找到一个真实能做的小行动，建立'我可以朝价值方向移动'的体验",
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "鼓励但不施压",
                    "pace_adjustment": "normal",
                    "culture_note": "行动建议应符合用户生活实际，避免理想化建议",
                },
                "contraindications": [
                    {"condition": "low_activation", "reason": "行动激活度极低时，先从 act_values 明确方向再行动"},
                    {"condition": "high_avoidance", "reason": "高回避时制定行动计划可能被体验为压力"},
                ],
            },
            state_effects={"session.act_intervention_log": "append"},
            success=True,
        )


# ── 注册到全局 skill_registry ─────────────────────────────────────
skill_registry.register(ActStateAssessment())
skill_registry.register(ActDefusion())
skill_registry.register(ActAcceptance())
skill_registry.register(ActPresentMoment())
skill_registry.register(ActSelfAsContext())
skill_registry.register(ActValues())
skill_registry.register(ActCommittedAction())
