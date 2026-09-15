"""Affect Labeling（情绪识别与命名）技能实现 —— 非疗法 Emotion Support。

两个技能：
- `affect_labeling_opportunity`：结合上下文判断此刻是否适合轻轻帮用户命名情绪，
  并生成 2–4 个有区分度的候选（LLM 判断 + 后端校验，规格第七、八节）；
- `affect_labeling_response`：把用户对上一问的回应归类（选了候选 / 自填 / 不知道 /
  拒绝 / 仍在讲述），提取用户自己的标签（规格第十、十一节）。

职责边界（规格第十四节）：只做「识别与命名」，不承担认知重构/解离/情绪调节/动机
访谈/例外寻找等功能；不做诊断；不强迫用户完成；不是问卷。
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
from src.skills.affect_lexicon import (
    CRISIS_CUES,
    NON_SUGGESTIBLE_TERMS,
    TOO_GENERIC_CANDIDATES,
)
from src.skills.prompts.affect import (
    AFFECT_OPPORTUNITY_OUTPUT_SCHEMA,
    AFFECT_OPPORTUNITY_PROMPT,
    AFFECT_RESPONSE_OUTPUT_SCHEMA,
    AFFECT_RESPONSE_PROMPT,
)

_VALID_KINDS = {"chosen", "custom", "unknown", "refused", "no_answer"}


# ═══════════════════════════════════════════════════════════════════════════════
# 候选校验（规格第八节：2–4、普通中文、无术语、有区分度、不暗示答案）
# ═══════════════════════════════════════════════════════════════════════════════

def _is_bad_candidate(c: str) -> bool:
    """候选是否不合格：含术语 / 过泛 / 危机词 / 空 / 过长无意义。"""
    if not c or not isinstance(c, str):
        return True
    c = c.strip()
    if not c:
        return True
    if len(c) > 12:  # 一句完整描述不应作为情绪标签
        return True
    # 临床术语 / 心理学术语 / 自我评价 / 事件 / DO_NOT_SUGGEST —— 一律不作为候选
    if any(t in c for t in NON_SUGGESTIBLE_TERMS):
        return True
    if any(t in c for t in CRISIS_CUES):
        return True
    if c in TOO_GENERIC_CANDIDATES:
        return True
    return False


def _clean_candidates(raw) -> list[str]:
    """去重保序 + 过滤不合格候选；不足 2 个视为无可提议。"""
    seen: list[str] = []
    if not isinstance(raw, list):
        return seen
    for item in raw:
        if not isinstance(item, str):
            continue
        c = item.strip()
        if not c or _is_bad_candidate(c):
            continue
        if c not in seen:
            seen.append(c)
    return seen[:4]


def _maybe_raw(raw, inputs: dict) -> dict:
    """评估用：`inputs["_include_raw_candidates"]=True` 时把原始候选原样透传。"""
    if not inputs.get("_include_raw_candidates"):
        return {}
    raw_list = [x for x in raw if isinstance(x, str)] if isinstance(raw, list) else []
    return {"_raw_candidates": raw_list[:8]}


class AffectOpportunity(LLMSkill):
    """机会评估：适合则 `should_offer=true` + 2–4 个候选（结合上下文生成）。"""

    name = "affect_labeling_opportunity"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Decide whether gentle affect labeling fits now and generate distinct candidate labels"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        history = inputs.get("history") or []
        profile = inputs.get("profile") or {}
        history_str = format_history(history[-6:])
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
        return f"""{AFFECT_OPPORTUNITY_PROMPT}

## Context

Recent conversation history (last 6 turns):
{history_str}

User profile summary:
{profile_str}

## Current User Message

{user_text}

## Output JSON
{AFFECT_OPPORTUNITY_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        should_offer = bool(data.get("should_offer", False))
        raw = data.get("candidates")
        candidates = _clean_candidates(raw)
        reason = str(data.get("reason", "") or "")
        if not should_offer or len(candidates) < 2:
            # 不触发是合法结论；候选不足 2 个时保守地不提议（绝不问 1 选项的问题）
            return SkillResult(
                skill_name=self.name,
                output={
                    "should_offer": False,
                    "candidates": [],
                    "reason": reason or "no offer",
                    # 评估用：原始候选透传（默认 absent，不影响正式输出形状）
                    **_maybe_raw(raw, inputs),
                },
                success=True,
            )
        return SkillResult(
            skill_name=self.name,
            output={
                "should_offer": True,
                "candidates": candidates,
                "reason": reason,
                **_maybe_raw(raw, inputs),
            },
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → 保守不触发（宁可漏，也不打扰用户）。"""
        return SkillResult(
            skill_name=self.name,
            output={"should_offer": False, "candidates": [], "reason": "opportunity_llm_failed"},
            success=True,
        )


class AffectResponseClassifier(LLMSkill):
    """把用户对上一问的回应归类为 chosen/custom/unknown/refused/no_answer。"""

    name = "affect_labeling_response"
    skill_type = SkillType.EMOTION_SUPPORT
    description = "Classify how the user responded to an affect-labeling offer"

    def build_prompt(self, inputs: dict) -> str:
        user_text = inputs.get("user_text", "")
        candidates = inputs.get("candidates") or []
        cand_str = json.dumps(candidates, ensure_ascii=False)
        return f"""{AFFECT_RESPONSE_PROMPT}

## 上次给用户的候选词
{cand_str}

## 用户这条回应
{user_text}

## Output JSON
{AFFECT_RESPONSE_OUTPUT_SCHEMA}"""

    def parse_output(self, data: dict, inputs: dict) -> SkillResult:
        kind = data.get("kind", "no_answer")
        if kind not in _VALID_KINDS:
            kind = "no_answer"
        candidates = inputs.get("candidates") or []
        matches = data.get("matches_candidate") or None
        if matches not in candidates:
            matches = None
        label = data.get("label") or None
        if isinstance(label, str):
            label = label.strip() or None
        # 与候选语义重合的归一化（用户的话 ≠ 候选字面，但意思一样）
        if matches:
            label = matches
        try:
            confidence = round(max(0.0, min(1.0, float(data.get("confidence", 0.5)))), 3)
        except (TypeError, ValueError):
            confidence = 0.5

        # 一致性收口：chosen / custom = 用户确认了一个标签
        if kind in ("chosen", "custom"):
            if kind == "chosen" and label not in candidates and matches is None:
                # LLM 报 chosen 却没落到任何候选：语义上更接近 custom 自填
                kind = "custom"
            user_confirmed = True
            user_correction = bool(
                kind == "custom" and label not in candidates
            )
        else:  # unknown / refused / no_answer
            user_confirmed = False
            user_correction = False
            label = None
            matches = None

        return SkillResult(
            skill_name=self.name,
            output={
                "kind": kind,
                "label": label,
                "matches_candidate": matches,
                "user_confirmed": user_confirmed,
                "user_correction": user_correction,
                "confidence": confidence,
            },
            success=True,
        )

    def fallback(self, inputs: dict) -> SkillResult:
        """LLM 失败 → 视为“仍在讲述/没直接回答”（engine 用轮次上限兜底收尾）。"""
        return SkillResult(
            skill_name=self.name,
            output={
                "kind": "no_answer",
                "label": None,
                "matches_candidate": None,
                "user_confirmed": False,
                "user_correction": False,
                "confidence": 0.0,
            },
            success=True,
        )


# ── 注册到全局 skill_registry（import src.skills 时统一触发） ─────
# 注意：非疗法技能——不属于任何疗法 Agent 的 FLOW/BRANCHES。
skill_registry.register(AffectOpportunity())
skill_registry.register(AffectResponseClassifier())
