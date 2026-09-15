"""Grounding（着陆/回到当下）技能实现 —— 非疗法 Emotion Support。

两个技能：
- `grounding_opportunity`：结合上下文判断此刻是否适合轻轻邀请一次「回到当下」
  的短练习（LLM 判断，规格：无 keyword→Grounding 硬映射）；
- `grounding_response`：把用户对邀请/引导步的回应归类（接受/拒绝/已好转/
  不适/转话题/暂停/模糊），供 engine 确定性推进或退出。

职责边界（设计规格）：只做「即时回到当下」——看见一个具体的东西 → 感受脚/手
与现实的接触 → 听一个周围的声音并确认所在环境 → check → complete。
不解释情绪、不命名情绪（Affect）、不引导肌肉收紧放松（PMR）、不做正念观察/
接纳/ дыхskills 等任何疗法技术（ACT Present Moment / DBT 的边界在 spec 文档）。
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
from src.skills.grounding_lexicon import CRISIS_CUES
from src.skills.prompts.grounding import (
    GROUNDING_OPPORTUNITY_OUTPUT_SCHEMA,
    GROUNDING_OPPORTUNITY_PROMPT,
    GROUNDING_RESPONSE_OUTPUT_SCHEMA,
    GROUNDING_RESPONSE_PROMPT,
)

_VALID_KINDS = {
    "affirmative", "negative", "stopped_feeling_better",
    "discomfort", "topic_shift", "pause", "other",
}


class GroundingOpportunity(LLMSkill):
    """机会评估：适合则 lightly offer 一次「回到当下」练习。"""

    name = "grounding_opportunity"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Decide whether a very short grounding (notice surroundings, feel contact, hear a sound) fits right now"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history") or []
        profile = inputs.get("profile") or {}
        history_str = format_history(history[-6:])
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""{GROUNDING_OPPORTUNITY_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

User profile summary:
{profile_str}

## Current User Message

{user_text}

## Output JSON
{GROUNDING_OPPORTUNITY_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        should_offer = bool(data.get("should_offer", False))
        reason = str(data.get("reason", "") or "")
        return SkillResult(
            skill_name=self.name,
            output={"should_offer": should_offer, "reason": reason or ("offer" if should_offer else "no offer")},
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → 保守不提议（宁可漏，也不打扰）。"""
        return SkillResult(
            skill_name=self.name,
            output={"should_offer": False, "reason": "opportunity_llm_failed"},
            success=True,
        )


class GroundingResponseClassifier(LLMSkill):
    """把用户对上一轮（邀请/引导步）的回应归类为 7 类之一。"""

    name = "grounding_response"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Classify how the user responded to a grounding offer or guidance step"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        step_label = inputs.get("step_label") or ""
        return f"""{GROUNDING_RESPONSE_PROMPT}

## 上一轮的情况
{step_label}

## 用户这条回应
{user_text}

## Output JSON
{GROUNDING_RESPONSE_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        kind = str(data.get("kind", "") or "").strip().lower()
        if kind not in _VALID_KINDS:
            kind = "other"
        return SkillResult(
            skill_name=self.name,
            output={"kind": kind, "reason": str(data.get("reason", "") or "")},
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → other（调用方按继续引导降级，不卡死）。"""
        return SkillResult(
            skill_name=self.name,
            output={"kind": "other", "reason": "response_llm_failed"},
            success=True,
        )


# 危机词表 re-export 便于 engine / 测试统一 import（与 PMR/affect 同一约定）
_GROUNDING_CRISIS_CUES = CRISIS_CUES

__all__ = ["GroundingOpportunity", "GroundingResponseClassifier"]


def _register() -> None:
    skill_registry.register(GroundingOpportunity())
    skill_registry.register(GroundingResponseClassifier())


_register()
