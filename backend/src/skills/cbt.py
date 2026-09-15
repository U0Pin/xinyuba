"""CBT（认知行为疗法）技能实现。

6 个技能：
- 2 个 ANALYSIS（LLM 驱动，Phase 3 起真实实现）：extract_automatic_thought、
  detect_cognitive_distortion
- 4 个 INTERVENTION（LLM 驱动）：generate_reframe、evaluate_evidence、
  decatastrophize、plan_behavioral_activation

LLM 用的 prompt / output schema 在 `src.skills.prompts.cbt`，
本文件只展示 skill 类的骨架与注册逻辑。

每个 INTERVENTION 类只需：
- 类名/描述、`prompt_template`、`output_schema`、`default_technique`、
  `valid_techniques`、`interaction_style`（元数据）
- `_context_block(inputs)`（构造"Context"段，因 family 而异）
- `fallback(inputs)`（LLM 失败时的兜底 SkillResult）
"""

import json

from src.core.skill import (
    LLMSkill,
    SkillResult,
    SkillType,
    TherapyInterventionSkill,
    skill_registry,
)
from src.skills.prompts.cbt import (
    REFRAME_PROMPT, REFRAME_OUTPUT_SCHEMA,
    EVIDENCE_PROMPT, EVIDENCE_OUTPUT_SCHEMA,
    DECATASTROPHIZE_PROMPT, DECATASTROPHIZE_OUTPUT_SCHEMA,
    ACTIVATION_PROMPT, ACTIVATION_OUTPUT_SCHEMA,
    EXTRACT_PROMPT, EXTRACT_OUTPUT_SCHEMA,
    DISTORTION_PROMPT, DISTORTION_OUTPUT_SCHEMA,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 技能（LLM 驱动，Phase 3 起真实实现）
# ═══════════════════════════════════════════════════════════════════════════════

class ExtractAutomaticThought(LLMSkill):
    """Skill 1.1：从用户话里抽取自动思维（LLM 驱动）。"""

    name = "extract_automatic_thought"
    skill_type = SkillType.ANALYSIS
    description = "Identify automatic thoughts embedded in user speech"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        return f"""{EXTRACT_PROMPT}

## User utterance

{user_text}

## Output JSON
{EXTRACT_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        raw_thoughts = data.get("automatic_thoughts") or []
        thoughts = []
        for t in raw_thoughts:
            if not isinstance(t, dict) or not t.get("thought"):
                continue
            thoughts.append({
                "thought": str(t["thought"]),
                "confidence": round(float(t.get("confidence", 0.5) or 0.5), 3),
                "span": t.get("span", ""),
                "target": t.get("target", "self"),
            })
        primary = data.get("primary_thought", "")
        if not primary and thoughts:
            primary = max(thoughts, key=lambda t: t["confidence"])["thought"]
        no_detected = bool(data.get("no_automatic_thought_detected", not thoughts))
        return SkillResult(
            skill_name=self.name,
            output={
                "automatic_thoughts": thoughts,
                "primary_thought": primary,
                "no_automatic_thought_detected": no_detected,
            },
            state_effects={"session.automatic_thoughts": "append"},
            success=True,
        )


class DetectCognitiveDistortion(LLMSkill):
    """Skill 1.2：把自动思维分类到 CBT 认知扭曲类目（LLM 驱动）。"""

    name = "detect_cognitive_distortion"
    skill_type = SkillType.ANALYSIS
    description = "Classify automatic thoughts into CBT distortion categories"

    _VALID_TYPES = {
        "catastrophizing", "mind_reading", "black_white_thinking",
        "overgeneralization", "self_blame", "emotional_reasoning",
        "should_statements", "labeling", "fortune_telling",
        "mental_filtering", "discounting_positive", "magnification",
        "personalization",
    }

    def _guard(self, inputs: dict, context: dict):
        """llm 缺失 → 错误；无自动思维 → 成功但空结果（不调 LLM）。"""
        llm = context.get("llm")
        if not llm:
            return None, SkillResult(
                skill_name=self.name, output={"error": "missing llm"}, success=False,
            )
        if not inputs.get("automatic_thoughts"):
            return None, SkillResult(
                skill_name=self.name,
                output={"classifications": [],
                        "distortion_summary": {"dominant_pattern": None,
                                               "distortion_count": 0,
                                               "severity": "none"}},
                success=True,
            )
        return llm, None

    def build_prompt(self, inputs: dict) -> str:
        thoughts = inputs.get("automatic_thoughts", [])
        thoughts_json = json.dumps(thoughts, ensure_ascii=False) if thoughts else "[]"
        return f"""{DISTORTION_PROMPT}

## Automatic thoughts

{thoughts_json}

## Output JSON
{DISTORTION_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        classifications = []
        for c in data.get("classifications") or []:
            if not isinstance(c, dict):
                continue
            distortions = []
            for d in c.get("distortions") or []:
                if not isinstance(d, dict) or d.get("type") not in self._VALID_TYPES:
                    continue
                distortions.append({
                    "type": d["type"],
                    "confidence": round(float(d.get("confidence", 0.5) or 0.5), 3),
                    "rationale": d.get("rationale", ""),
                })
            classifications.append({
                "thought": c.get("thought", ""),
                "distortions": distortions,
                "primary_distortion": c.get("primary_distortion")
                or (max(distortions, key=lambda d: d["confidence"])["type"] if distortions else None),
            })
        summary = data.get("distortion_summary") or {}
        count = int(summary.get("distortion_count") or 0)
        if not count:
            count = sum(len(c["distortions"]) for c in classifications)
        return SkillResult(
            skill_name=self.name,
            output={
                "classifications": classifications,
                "distortion_summary": {
                    "dominant_pattern": summary.get("dominant_pattern"),
                    "distortion_count": count,
                    "severity": summary.get("severity", "none"),
                },
            },
            state_effects={"session.distortion_analysis": "write",
                           "profile.distortion_history": "append"},
            success=True,
        )

# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION 技能（LLM 驱动，共享 CbtIntervention 基类）
# ═══════════════════════════════════════════════════════════════════════════════


class CbtIntervention(TherapyInterventionSkill):
    """CBT INTERVENTION 技能的家族基类：共享 state_effects 和兜底 schema。"""

    state_effects_value = {"session.cbt_intervention_log": "append"}

class GenerateReframe(CbtIntervention):
    """generate_reframe：为一个被用户固化的负面解读生成替代解释。"""
    name = "generate_reframe"
    skill_type = SkillType.INTERVENTION
    description = "Generate multiple alternative interpretations for an automatic thought"

    prompt_template = REFRAME_PROMPT
    output_schema = REFRAME_OUTPUT_SCHEMA
    default_technique = "alternative_causes"
    valid_techniques = ["alternative_causes", "decatastrophized", "evidence_based", "perspective_taking"]
    interaction_style = "exploratory"

    def _context_block(self, inputs: dict) -> str:
        # CBT 重构类技能聚焦"目标思维"和"已识别的认知扭曲"，让 LLM
        # 围绕这两者构造替代解释 / 证据 / 反灾难化方案。
        target_thought = inputs.get("target_thought", "")
        user_text = inputs.get("user_text", "")
        assessment = inputs.get("assessment", {})
        thought = target_thought or user_text
        dist_info = json.dumps(assessment.get("distortions", []), ensure_ascii=False) if assessment else "[]"
        return f"""Target thought: {thought}
Detected distortions: {dist_info}"""

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败时的兜底：保守地给出"寻找其他可能性"方案。"""
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": "alternative_causes",
                    "description": "Generate plausible alternative explanations for the situation",
                    "steps": [
                        "List 2-3 alternative explanations for what happened",
                        "Invite the user to weigh each alternative against their first interpretation",
                        "Let the user choose which interpretation feels most accurate",
                    ],
                },
                "conversation_goal": "帮助用户看到：当前对事件的解释只是众多可能性之一",
                "adaptation": {
                    "interaction_style": "exploratory",
                    "tone_adjustment": "好奇、不评判",
                    "pace_adjustment": "normal",
                    "culture_note": "用'可能还有别的解释'替代'你想错了'",
                },
                "contraindications": [
                    {"condition": "high_emotion", "reason": "强烈情绪下应先稳定情绪，再做认知重构"},
                ],
            },
            state_effects={"session.cbt_intervention_log": "append"},
            success=True,
        )


class EvaluateEvidence(CbtIntervention):
    """evaluate_evidence：帮用户平衡地检视支持与反对某个自动思维的证据。"""
    name = "evaluate_evidence"
    skill_type = SkillType.INTERVENTION
    description = "Evaluate evidence for and against an automatic thought"

    prompt_template = EVIDENCE_PROMPT
    output_schema = EVIDENCE_OUTPUT_SCHEMA
    default_technique = "pros_cons_table"
    valid_techniques = ["pros_cons_table", "evidence_for_against", "behavioral_experiment"]
    interaction_style = "exploratory"

    def _context_block(self, inputs: dict) -> str:
        target_thought = inputs.get("target_thought", "")
        user_text = inputs.get("user_text", "")
        assessment = inputs.get("assessment", {})
        thought = target_thought or user_text
        dist_info = json.dumps(assessment.get("distortions", []), ensure_ascii=False) if assessment else "[]"
        return f"""Target thought: {thought}
Detected distortions: {dist_info}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": "pros_cons_table",
                    "description": "Help the user weigh what supports and contradicts the thought",
                    "steps": [
                        "邀请用户列出支持这个想法的具体事实",
                        "邀请用户列出反对这个想法的具体事实",
                        "帮助用户看到两边证据的分布",
                    ],
                },
                "conversation_goal": "帮助用户区分'事实'和'想法'，看到自己忽略的证据",
                "adaptation": {
                    "interaction_style": "exploratory",
                    "tone_adjustment": "像调查员一样好奇，不站队",
                    "pace_adjustment": "normal",
                    "culture_note": "用'支持这个想法的事实'和'反对这个想法的事实'代替'pro/con'",
                },
                "contraindications": [
                    {"condition": "high_distress", "reason": "高痛苦时认知能力下降，先稳定情绪"},
                ],
            },
            state_effects={"session.cbt_intervention_log": "append"},
            success=True,
        )


class Decatastrophize(CbtIntervention):
    """decatastrophize：把灾难化的"完了"链条一步一步拆开看。"""
    name = "decatastrophize"
    skill_type = SkillType.INTERVENTION
    description = "Systematically break down a catastrophic prediction chain"

    prompt_template = DECATASTROPHIZE_PROMPT
    output_schema = DECATASTROPHIZE_OUTPUT_SCHEMA
    default_technique = "chain_analysis"
    valid_techniques = ["chain_analysis", "probability_reframing", "coping_response"]
    interaction_style = "exploratory"

    def _context_block(self, inputs: dict) -> str:
        # 去灾难化聚焦已识别的扭曲集合，不需要用户原文（避免被用户措辞再次触发）。
        assessment = inputs.get("assessment", {})
        dist_info = json.dumps(assessment.get("distortions", []), ensure_ascii=False) if assessment else "[]"
        return f"""Detected distortions: {dist_info}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": "chain_analysis",
                    "description": "Walk through the feared chain and find the weakest links",
                    "steps": [
                        "把'最坏会发生什么'列成一步步的链条",
                        "在每一节点问：这个真的会发生吗？还有什么可能？",
                        "帮助用户看到链条中至少有一个节点并不那么确定",
                    ],
                },
                "conversation_goal": "帮助用户看到'最坏'并不是唯一可能，链条上的每个节点都有不确定性",
                "adaptation": {
                    "interaction_style": "exploratory",
                    "tone_adjustment": "好奇、稳",
                    "pace_adjustment": "normal",
                    "culture_note": "用'接下来会发生什么'一步步问，避免'想太多'等评价",
                },
                "contraindications": [
                    {"condition": "panic_level_anxiety", "reason": "惊恐水平下认知重组无效，先 grounding"},
                ],
            },
            state_effects={"session.cbt_intervention_log": "append"},
            success=True,
        )


class PlanBehavioralActivation(CbtIntervention):
    """plan_behavioral_activation：为退缩/低动力的用户设计逐步活动计划。"""
    name = "plan_behavioral_activation"
    skill_type = SkillType.INTERVENTION
    description = "Design graduated activities to counter withdrawal and low mood"

    prompt_template = ACTIVATION_PROMPT
    output_schema = ACTIVATION_OUTPUT_SCHEMA
    default_technique = "activity_scheduling"
    valid_techniques = ["activity_scheduling", "mastery_pleasure", "small_step_ladder"]
    interaction_style = "supportive"

    def _context_block(self, inputs: dict) -> str:
        # 行为激活聚焦"当前激活度"，让 LLM 根据用户现在的状态定计划。
        assessment = inputs.get("assessment", {})
        act_info = json.dumps(assessment.get("activation", {}), ensure_ascii=False) if assessment else "{}"
        return f"""Activation level: {act_info}"""

    def fallback(self, inputs: dict) -> SkillResult:
        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": "small_step_ladder",
                    "description": "Break meaningful activities into tiny, doable steps",
                    "steps": [
                        "找一个用户觉得有一点点意义的小事",
                        "把这件事拆成 5 分钟以内能开始的一步",
                        "邀请用户：现在或者今天，就从这一步开始",
                    ],
                },
                "conversation_goal": "帮助用户迈出极小的一步，让'我能做点什么'的体验先于动力恢复",
                "adaptation": {
                    "interaction_style": "supportive",
                    "tone_adjustment": "不施压、温和",
                    "pace_adjustment": "very_slow",
                    "culture_note": "从'吃一顿饭' '出门走走'等最日常的开始；'哪怕做了 1% 也是做了'",
                },
                "contraindications": [
                    {"condition": "crisis", "reason": "危机状态下不适合制定计划，先稳定"},
                    {"condition": "severe_depression", "reason": "重度抑郁时先就医，行为激活辅助而非替代"},
                ],
            },
            state_effects={"session.cbt_intervention_log": "append"},
            success=True,
        )


# ── 注册到全局 skill_registry（import src.skills 时统一触发） ─────
skill_registry.register(ExtractAutomaticThought())
skill_registry.register(DetectCognitiveDistortion())
skill_registry.register(GenerateReframe())
skill_registry.register(EvaluateEvidence())
skill_registry.register(Decatastrophize())
skill_registry.register(PlanBehavioralActivation())
