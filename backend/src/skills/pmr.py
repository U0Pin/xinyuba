"""PMR（渐进式肌肉放松）技能实现 —— 非疗法 Emotion Support。

两个技能：
- `pmr_opportunity`：结合上下文判断此刻是否适合轻轻邀请一次短放松，并给出可聚焦的
  紧绷部位（shoulders/hands/face/general）——是否触发由 LLM 判断，代码 cue 只是预筛；
- `pmr_response`：把用户对上一轮（邀请或引导拍）的回应归类为 affirmative / neutral /
  negative / pause / discomfort / cannot_tense / topic_shift / other。

职责边界：只做「此刻适不适合松一下身体 + 回应的轻量理解」；不做诊断、不教呼吸/冥想、
不做认知或情绪分析；指导收放属于引擎与 Host 的对话层，不在此处产出治疗性语言。
"""

from __future__ import annotations

import json

from src.core.skill import (
    LLMSkill,
    SkillResult,
    SkillType,
    format_history,
    skill_registry,
)
from src.skills.prompts.pmr import (
    PMR_OPPORTUNITY_OUTPUT_SCHEMA,
    PMR_OPPORTUNITY_PROMPT,
    PMR_RESPONSE_OUTPUT_SCHEMA,
    PMR_RESPONSE_PROMPT,
)

# ── 支持引导的身体区域（唯一权威）：标签 + 顺序 ──────────────────────
# 默认从 shoulders 开始，必要时由 opportunity 的 body_focus 决定起始部位。
PMR_BODY_PARTS: dict[str, str] = {
    "shoulders": "肩膀",
    "hands": "双手",
    "face": "脸",
}
PMR_BODY_ORDER: tuple[str, ...] = tuple(PMR_BODY_PARTS)
_VALID_FOCUS = {*PMR_BODY_ORDER, "general"}
_VALID_KINDS = {
    "affirmative", "neutral", "negative", "pause",
    "discomfort", "cannot_tense", "topic_shift", "other",
}


class PmrOpportunity(LLMSkill):
    """机会评估：适合则 `should_offer=true` + 可选紧绷部位聚焦（决定从哪开始）。"""

    name = "pmr_opportunity"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Decide whether a short progressive-muscle-relaxation offer fits now and which body part to focus"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history") or []
        profile = inputs.get("profile") or {}
        history_str = format_history(history[-6:])
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""{PMR_OPPORTUNITY_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

User profile summary:
{profile_str}

## Current User Message

{user_text}

## Output JSON
{PMR_OPPORTUNITY_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        should_offer = bool(data.get("should_offer", False))
        focus = str(data.get("body_focus", "") or "").strip()
        if focus not in _VALID_FOCUS:
            focus = "general"
        reason = str(data.get("reason", "") or "")
        if not should_offer:
            return SkillResult(
                skill_name=self.name,
                output={"should_offer": False, "body_focus": "general",
                        "reason": reason or "no offer"},
                success=True,
            )
        return SkillResult(
            skill_name=self.name,
            output={"should_offer": True, "body_focus": focus, "reason": reason},
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → 保守不提议（宁可漏，也不打扰）。"""
        return SkillResult(
            skill_name=self.name,
            output={"should_offer": False, "body_focus": "general",
                    "reason": "opportunity_llm_failed"},
            success=True,
        )


class PmrResponseClassifier(LLMSkill):
    """把用户对上一轮（邀请/引导拍）的回应归类为 8 类之一。"""

    name = "pmr_response"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Classify how the user responded to a PMR offer or guidance beat"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        situation = inputs.get("situation") or ""
        return f"""{PMR_RESPONSE_PROMPT}

## 上一轮的情况
{situation}

## 用户这条回应
{user_text}

## Output JSON
{PMR_RESPONSE_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        kind = data.get("kind", "other")
        if kind not in _VALID_KINDS:
            kind = "other"
        reason = str(data.get("reason", "") or "")
        return SkillResult(
            skill_name=self.name,
            output={"kind": kind, "reason": reason},
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → 保守地返回 other（stage 语义由引擎决定：offer 不开始，active 默认继续）。"""
        return SkillResult(
            skill_name=self.name,
            output={"kind": "other", "reason": "response_llm_failed"},
            success=True,
        )


# ── 注册到全局 skill_registry（import src.skills 时统一触发） ─────
# 注意：非疗法技能——不属于任何疗法 Agent 的 FLOW/BRANCHES。
skill_registry.register(PmrOpportunity())
skill_registry.register(PmrResponseClassifier())
