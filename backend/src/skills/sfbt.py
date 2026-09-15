"""SFBT（焦点解决短期治疗）技能实现。

4 个技能：
- 1 个 ANALYSIS（LLM 驱动）：sfbt_state_assessment —— 3 维评估，
  其中 `sfbt_intervention_need` 由后端公式计算（不由 LLM 生成）
- 3 个 INTERVENTION（LLM 驱动）：sfbt_resource_exploration、
  sfbt_exception_exploration、sfbt_future_construction

LLM 用的 prompt / output schema / 状态阈值在 `src.skills.prompts.sfbt`，
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
    SFBTProcess,
    SFBTResourceTechnique,
    SFBTExceptionTechnique,
    SFBTActionTechnique,
)
from src.skills.prompts.sfbt import (
    SFBT_ASSESSMENT_PROMPT, SFBT_ASSESSMENT_OUTPUT_SCHEMA,
    SFBT_RESOURCE_PROMPT, SFBT_RESOURCE_OUTPUT_SCHEMA,
    SFBT_EXCEPTION_PROMPT, SFBT_EXCEPTION_OUTPUT_SCHEMA,
    SFBT_FUTURE_PROMPT, SFBT_FUTURE_OUTPUT_SCHEMA,
    _GC_STATES, _RA_STATES, _PO_STATES,
)


def _compute_sfbt_intervention_need(
    goal_clarity: float,
    resource_awareness: float,
    problem_stuck_value: float,
) -> float:
    """后端公式计算 SFBT 干预需求 —— 不由 LLM 生成。

    只有"卡在问题中"（problem_stuck）被扣分，正常的"描述问题"不惩罚。
    权重设计：
    - 0.35 × (1 - 目标清晰)（越模糊越需要未来建构）
    - 0.30 × (1 - 资源意识)（越盲目越需要资源探索）
    - 0.35 × problem_stuck_value（越卡在问题里越需要例外探索）
    """
    return round(
        0.35 * (1 - goal_clarity)
        + 0.30 * (1 - resource_awareness)
        + 0.35 * problem_stuck_value,
        3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS 技能：sfbt_state_assessment
# ═══════════════════════════════════════════════════════════════════════════════

class SfbtStateAssessment(LLMSkill):
    """SFBT 3 维状态评估 —— 由 LLM 输出 3 个索引，再由后端公式算出干预需求。"""
    name = "sfbt_state_assessment"
    skill_type = SkillType.ANALYSIS
    description = "LLM-driven SFBT state assessment across 3 dimensions with confidence and recommendations"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history", [])
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        prev_sfbt_state = inputs.get("current_sfbt_state")
        history_str = format_history(history[-6:])
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        prev_str = json.dumps(prev_sfbt_state, ensure_ascii=False) if prev_sfbt_state else "no previous assessment"

        return f"""{SFBT_ASSESSMENT_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

Emotional state (from Emotion Agent):
{emotion_str}

User profile summary:
{profile_str}

Previous SFBT assessment:
{prev_str}

## Current User Message

{user_text}

## Output JSON

{SFBT_ASSESSMENT_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        # 提取 3 维索引并夹紧到 [0, 1]
        gc_idx = max(0.0, min(1.0, float(data.get("goal_clarity", {}).get("index", 0.0))))
        ra_idx = max(0.0, min(1.0, float(data.get("resource_awareness", {}).get("index", 0.0))))
        po_idx = max(0.0, min(1.0, float(data.get("problem_orientation", {}).get("index", 0.0))))
        po_state = discrete_state(po_idx, _PO_STATES, inclusive=True)

        # 关键语义：只有"卡在问题中"（problem_stuck）被惩罚，普通描述问题的
        # 用户不会因此推高干预需求。这是 SFBT 的核心原则。
        problem_stuck_value = po_idx if po_state == "problem_stuck" else 0.0

        # 后端公式（不由 LLM 决定）
        priority = _compute_sfbt_intervention_need(gc_idx, ra_idx, problem_stuck_value)

        # 校验 recommended_processes.process 值在 SFBTProcess 枚举内
        valid_processes = {p.value for p in SFBTProcess}
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

        # should_intervene：多条件触发
        should_intervene = (
            priority > 0.30
            or gc_idx <= 0.40
            or problem_stuck_value >= 0.60
        )

        output = {
            "goal_clarity": {
                "index": round(gc_idx, 3),
                "state": discrete_state(gc_idx, _GC_STATES, inclusive=True),
                "confidence": round(float(data.get("goal_clarity", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("goal_clarity", {}).get("evidence", []),
            },
            "resource_awareness": {
                "index": round(ra_idx, 3),
                "state": discrete_state(ra_idx, _RA_STATES, inclusive=True),
                "confidence": round(float(data.get("resource_awareness", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("resource_awareness", {}).get("evidence", []),
            },
            "problem_orientation": {
                "index": round(po_idx, 3),
                "state": po_state,
                "confidence": round(float(data.get("problem_orientation", {}).get("confidence", 0.5)), 3),
                "evidence": data.get("problem_orientation", {}).get("evidence", []),
            },
            "sfbt_intervention_need": priority,
            "overall_trend": data.get("overall_trend", "stable"),
            "primary_concern": data.get("primary_concern", ""),
            "should_intervene": should_intervene,
            "recommended_processes": sanitized_processes,
        }

        return SkillResult(
            skill_name=self.name,
            output=output,
            state_effects={"session.sfbt_state": "write"},
            success=True,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INTERVENTION 技能（共享 SfbtIntervention 基类）
# ═══════════════════════════════════════════════════════════════════════════════

class SfbtIntervention(TherapyInterventionSkill):
    """SFBT INTERVENTION 技能的家族基类。

    SFBT 家族特有：所有技能固定用 hopeful + future_oriented 的 adaptation，
    不由 LLM 的 tone/pace 输出决定 —— 这是 SFBT 的声音基调设计。
    """

    state_effects_value = {"session.sfbt_intervention_log": "append"}
    interaction_style = InteractionStyle.EXPLORATORY.value

    def _adaptation(self, data: dict) -> dict:
        """SFBT 家族固定 hopeful / future_oriented 基调。"""
        return {
            "interaction_style": self.interaction_style,
            "tone_adjustment": "hopeful",
            "pace_adjustment": "future_oriented",
            "culture_note": data.get("adaptation", {}).get("culture_note", ""),
        }

class SfbtResourceExploration(SfbtIntervention):
    """sfbt_resource_exploration：发现用户已有的应对能力和优势资源。"""
    name = "sfbt_resource_exploration"
    skill_type = SkillType.INTERVENTION
    description = "Discover strengths: coping questions and compliments/strengths identification"

    prompt_template = SFBT_RESOURCE_PROMPT
    output_schema = SFBT_RESOURCE_OUTPUT_SCHEMA
    default_technique = SFBTResourceTechnique.COPING_QUESTIONS.value
    valid_techniques = [t.value for t in SFBTResourceTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 资源探索同时看"资源意识"和"问题方向"—— 资源意识越低越需要 coping_questions。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        ra_info = json.dumps(assessment.get("resource_awareness", {}), ensure_ascii=False)
        po_info = json.dumps(assessment.get("problem_orientation", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Resource awareness: {ra_info}
Problem orientation: {po_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 资源意识低/盲 → coping_questions（问怎么撑过来的）；
        # 否则 → compliments_strengths（具体指出优势）。
        assessment = inputs.get("assessment", {})
        ra_state = assessment.get("resource_awareness", {}).get("state", "blind")
        if ra_state in ("blind", "low"):
            tech = SFBTResourceTechnique.COPING_QUESTIONS
            goal = "帮助用户发现自己已经拥有的应对能力，看到'这么难还在坚持'本身就是力量"
        else:
            tech = SFBTResourceTechnique.COMPLIMENTS_STRENGTHS
            goal = "通过具体的观察和肯定，帮助用户看到自己身上自己没注意到的优势和资源"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user discover their existing strengths and coping abilities",
                    "steps": [
                        "好奇地询问用户是怎么撑过困难的",
                        "用具体的观察肯定用户的努力和韧性",
                        "帮助用户把'幸存下来'重新定义为'我比我想象的更有能力'",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "hopeful",
                    "pace_adjustment": "future_oriented",
                    "culture_note": "用'这么难你是怎么撑过来的'替代'你很坚强'；具体肯定优于笼统赞美；不强迫用户接受赞美",
                },
                "contraindications": [
                    {"condition": "crisis", "reason": "危机状态下的赞美可能被体验为 dismissive，先做 grounding"}
                ],
            },
            state_effects={"session.sfbt_intervention_log": "append"},
            success=True,
        )


class SfbtExceptionExploration(SfbtIntervention):
    """sfbt_exception_exploration：找到问题不在/较弱的例外时刻。"""
    name = "sfbt_exception_exploration"
    skill_type = SkillType.INTERVENTION
    description = "Find exceptions: exception_finding and scaling_questions to reveal solution patterns"

    prompt_template = SFBT_EXCEPTION_PROMPT
    output_schema = SFBT_EXCEPTION_OUTPUT_SCHEMA
    default_technique = SFBTExceptionTechnique.EXCEPTION_FINDING.value
    valid_techniques = [t.value for t in SFBTExceptionTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 例外探索同时看"目标清晰度"和"问题方向"。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        gc_info = json.dumps(assessment.get("goal_clarity", {}), ensure_ascii=False)
        po_info = json.dumps(assessment.get("problem_orientation", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Goal clarity: {gc_info}
Problem orientation: {po_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 问题主导/卡住 → exception_finding（找例外）；否则 → scaling_questions（量尺）。
        assessment = inputs.get("assessment", {})
        po_state = assessment.get("problem_orientation", {}).get("state", "balanced")
        if po_state in ("problem_dominant", "problem_stuck"):
            tech = SFBTExceptionTechnique.EXCEPTION_FINDING
            goal = "帮助用户找到问题不在或不那么严重的时刻，从中发现已有的解决模式"
        else:
            tech = SFBTExceptionTechnique.SCALING_QUESTIONS
            goal = "通过量尺评分帮助用户看到进展和不同，建立改变的信心"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user discover times when the problem was not present or was handled differently",
                    "steps": [
                        "邀请用户回忆不一样的时刻",
                        "探索那个时刻有什么不同——用户做了什么、想了什么、发生了什么",
                        "帮助用户把自己的行动与积极结果联系起来",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "hopeful",
                    "pace_adjustment": "future_oriented",
                    "culture_note": "用'不一样的时刻'替代'例外'（避免暗示反常）；'为什么不是更低'比'为什么不是更高'更有力",
                },
                "contraindications": [
                    {"condition": "crisis_intense", "reason": "用户处于强烈痛苦中时，问例外可能被体验为不共情，先倾听和确认"}
                ],
            },
            state_effects={"session.sfbt_intervention_log": "append"},
            success=True,
        )


class SfbtFutureConstruction(SfbtIntervention):
    """sfbt_future_construction：通过奇迹提问描绘愿景 + 一小步行动。"""
    name = "sfbt_future_construction"
    skill_type = SkillType.INTERVENTION
    description = "Build the future: miracle question for vision, small_step for concrete action"

    prompt_template = SFBT_FUTURE_PROMPT
    output_schema = SFBT_FUTURE_OUTPUT_SCHEMA
    default_technique = SFBTActionTechnique.MIRACLE_QUESTION.value
    valid_techniques = [t.value for t in SFBTActionTechnique]
    interaction_style = InteractionStyle.SUPPORTIVE.value

    def _context_block(self, inputs: dict) -> str:
        # 未来建构同时看"目标清晰度"和"资源意识"—— 两者都高就适合 small_step，
        # 否则先 miracle_question 把愿景画出来。
        assessment = inputs.get("assessment", {})
        emotion = inputs.get("emotion", {})
        profile = inputs.get("profile", {})
        gc_info = json.dumps(assessment.get("goal_clarity", {}), ensure_ascii=False)
        ra_info = json.dumps(assessment.get("resource_awareness", {}), ensure_ascii=False)
        emotion_str = json.dumps(emotion, ensure_ascii=False)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""Goal clarity: {gc_info}
Resource awareness: {ra_info}
Emotional state: {emotion_str}
User profile: {profile_str}"""

    def fallback(self, inputs: dict) -> SkillResult:
        # 目标模糊/刚浮现 → miracle_question（先画愿景）；否则 → small_step。
        assessment = inputs.get("assessment", {})
        gc_state = assessment.get("goal_clarity", {}).get("state", "vague")
        if gc_state in ("vague", "emerging"):
            tech = SFBTActionTechnique.MIRACLE_QUESTION
            goal = "通过奇迹提问帮助用户描绘理想的未来图景，理清自己真正想要的改变"
        else:
            tech = SFBTActionTechnique.SMALL_STEP
            goal = "帮助用户从愿景中提取一个具体、可操作的小步骤，从现在开始行动"

        return SkillResult(
            skill_name=self.name,
            output={
                "technique": {
                    "name": tech.value,
                    "description": "Help user envision preferred future and take a concrete small step",
                    "steps": [
                        "邀请用户想象问题解决后的具体画面",
                        "帮助用户将愿景转化为具体的日常改变",
                        "邀请用户选择一个最小、最具体的行动步骤",
                    ],
                },
                "conversation_goal": goal,
                "adaptation": {
                    "interaction_style": InteractionStyle.SUPPORTIVE.value,
                    "tone_adjustment": "hopeful",
                    "pace_adjustment": "future_oriented",
                    "culture_note": "奇迹提问需温和铺垫：'这不是算命，只是帮你想清楚你真正想要什么'；'从小事做起'天然契合中国文化",
                },
                "contraindications": [
                    {"condition": "crisis", "reason": "危机状态下未来的想象可能引发焦虑，先做 grounding"},
                    {"condition": "goal_too_vague", "reason": "目标过于模糊时，先通过量尺或例外探索帮助澄清"},
                ],
            },
            state_effects={"session.sfbt_intervention_log": "append"},
            success=True,
        )


# ── 注册到全局 skill_registry ─────────────────────────────────────
skill_registry.register(SfbtStateAssessment())
skill_registry.register(SfbtResourceExploration())
skill_registry.register(SfbtExceptionExploration())
skill_registry.register(SfbtFutureConstruction())
