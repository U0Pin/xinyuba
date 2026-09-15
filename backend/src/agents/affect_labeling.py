"""Affect Labeling 决策引擎（新架构，决策线可选依赖）。

定位（规格第三、十五、十六节）：非疗法 Emotion Support。优先级永远低于
危机/安全与当前疗法流程——本引擎只在「daily 无 crisis 无 therapy 且 risk 非高危」
的轮次被调用（由 DecisionLine / Scheduler 决定），engine 内部再自守一道。

两阶段职责分离：
- `maybe_plan(ctx)`：在本轮回复**开始前**被 Scheduler 串行调用（同轮提问时序）。
  纯代码 gate 命中才跑一次机会 LLM（候选生成），产出 plan 供对话线在**本轮回复**
  里带出候选提问；**不写状态**（单写者纪律：state 仍由 DecisionLine 独占写）。
- `run(ctx, *, switched_to_therapy)`：在本轮决策末尾被 DecisionLine 调用，把
  结构化结论落盘到 `state.affect_labeling`：
    · 本轮刚提议（ctx.affect_labeling_plan 非空）→ 写 `proposing`；
    · 上轮已 proposing → 归类用户当前回应（选了候选/自填/纠正/不知道/拒绝/仍在讲），
      写 `closed` + outcome，或收尾（no_response）不再追问；
    · 本轮切到疗法 / 进入高危 → 清残留。
  只返回要并入 state 的更新 dict（无变化返回 {}）。
"""

from __future__ import annotations

import re
from typing import Optional

from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.core.scheduler import TurnContext
from src.core.skill import skill_registry
from src.core.state import RiskState
from src.skills.affect_lexicon import (
    CRISIS_CUES,
    GENERALIZED_AFFECT_TERMS,
    INCOMPLETE_NAMING_CUES,
    LOW_SPECIFICITY_CUES,
    SELF_CONFUSION_PATTERNS,
    SOLUTION_CONFUSION_CUES,
    SPECIFIC_EMOTION_TERMS,
)
from src.store.session_store import SessionStore
from src.utils.text import history_pairs

import src.skills  # noqa: F401 — 确保技能已注册（副作用 import）

_CLOSED_REASON_ANSWERED = "answered"
_CLOSED_REASON_NO_RESPONSE = "no_response"
_CLOSED_REASON_SUPERSEDED = "superseded"

# 关闭后的冷却（以流水条数计，粗略换算约 3 轮对话），避免同一会话反复触发
_COOLDOWN_SEQS = 6

# 情感指涉参照（供 INCOMPLETE 命名 cue 参与 gate 时做“同句确有情绪线索”的守卫，
# 避免“也不知道”之类的弱 cue 在闲聊里无条件触发；硬 exclusion 仍最先执行）。
_AFFECT_REFERENCE = (
    frozenset(LOW_SPECIFICITY_CUES)
    | frozenset(GENERALIZED_AFFECT_TERMS)
    | SPECIFIC_EMOTION_TERMS
)


class AffectLabelingEngine:
    """把 affect_labeling 的机会评估 / 回应归类翻译成调度状态的最小引擎。"""

    def __init__(
        self,
        llm: LLMClient,
        session_store: Optional[SessionStore] = None,
        cooldown_seqs: int = _COOLDOWN_SEQS,
    ):
        self._llm = llm
        self.sessions = session_store or SessionStore()
        self.cooldown_seqs = cooldown_seqs

    # ── 技能句柄（注册可能晚于模块 import，惰性取） ─────────────────

    def _opportunity_skill(self):
        return skill_registry.get("affect_labeling_opportunity")

    def _response_skill(self):
        return skill_registry.get("affect_labeling_response")

    # ═══════════════════════════════════════════════════════════════════════════
    # 触发预筛 gate（纯代码、零 LLM；只决定“是否值得跑一次机会 LLM”，非诊断）
    # ═══════════════════════════════════════════════════════════════════════════

    def _gate(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if any(cue in t for cue in CRISIS_CUES):
            return False  # 危机让位安全流程（最高优先的 hard exclusion）
        # 低区分度情绪表达 → 值得帮 ta 命名
        if any(cue in t for cue in LOW_SPECIFICITY_CUES):
            return True
        # 命名未完成（INCOMPLETE）参与 gate：仅当同句确有情感/状态指涉时才作为澄清信号，
        # 避免“也不知道”等弱 cue 在闲聊/无情绪文本里无条件触发（保持非 hard 直觉）。
        if any(cue in t for cue in INCOMPLETE_NAMING_CUES) and any(
            w in t for w in _AFFECT_REFERENCE
        ):
            return True
        # 对自己情绪/状态的困惑（注意与“该怎么办/怎么准备”的决策困惑区分开）
        for pat in SELF_CONFUSION_PATTERNS:
            if pat in t:
                return True
        # 兼容“我不知道……”+ 情感状态困惑的自由组合（排除行动/方案困惑）
        if "不知道" in t and not any(cue in t for cue in SOLUTION_CONFUSION_CUES):
            if re.search(r"(怎么|为什么|咋|哪来|哪里来|怎么回事)", t):
                return True
        return False

    # ═══════════════════════════════════════════════════════════════════════════
    # 机会评估（对话前串行；不写状态）
    # ═══════════════════════════════════════════════════════════════════════════

    def _closed_recent(self, ctx: TurnContext) -> bool:
        al = ctx.state.affect_labeling
        if not al or al.get("status") != "closed":
            return False
        closed_seq = int(al.get("closed_seq") or 0)
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        return closed_seq > 0 and (now_seq - closed_seq) <= self.cooldown_seqs

    async def maybe_plan(self, ctx: TurnContext) -> Optional[dict]:
        """本轮回复前的机会评估。返回 plan（含 candidates）或 None。

        调用方（Scheduler）已保证：owner=daily、无 crisis、无 therapy、engine 装配。
        这里再自守：risk 高危 / 消息为空 / 进行中标注 / 冷却期 / gate 未命中 → None。
        """
        state = ctx.state
        if state.crisis or state.therapy is not None:
            return None
        if state.risk_level in (RiskState.HIGH_RISK, RiskState.CRISIS):
            return None
        al = state.affect_labeling
        if al and al.get("status") == "proposing":
            return None  # 提问已发出：本轮交给 run() 归类回应，不再另开新问题
        if not self._gate(ctx.message):
            return None
        if self._closed_recent(ctx):
            return None

        skill = self._opportunity_skill()
        if skill is None:
            return None
        tail = history_pairs(self.sessions.read_flow_tail(ctx.session_id, 8))
        bound = self._llm.bind(
            agent="affect_labeling_opportunity",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        try:
            result = await skill.aexecute(
                {
                    "user_text": ctx.message,
                    "history": tail,
                    "profile": ctx.profile or {},
                },
                {"llm": bound},
            )
        except Exception as e:
            app_log("warning", "affect", "opportunity_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return None
        if not result.success or not result.output.get("should_offer"):
            return None
        candidates = result.output.get("candidates") or []
        if len(candidates) < 2:
            return None
        return {"candidates": candidates, "reason": result.output.get("reason", "")}

    # ═══════════════════════════════════════════════════════════════════════════
    # 决策末尾落盘（DecisionLine 独占写 state 的通道之一）
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(self, ctx: TurnContext, *, switched_to_therapy: bool) -> dict:
        """返回要并入 state 的 affect_labeling 更新；无变化返回 {}。"""
        al = ctx.state.affect_labeling or {}
        now_seq = self.sessions.next_flow_seq(ctx.session_id)

        # 本轮切到疗法 / 危机已被上层 early return / 高危 → 清残留（疗法与安全优先）
        if switched_to_therapy:
            if al:
                return {"affect_labeling": None}
            return {}

        # ① 本轮刚判定要提议（同轮提问已发生，落盘 proposing 供下一轮对话线带候选）
        plan = ctx.affect_labeling_plan
        if plan:
            proposing = {
                "status": "proposing",
                "candidates": list(plan.get("candidates") or []),
                "start_seq": now_seq,
                "reason": plan.get("reason", ""),
            }
            app_log("info", "affect", "proposed", trace_id=ctx.trace_id,
                    session_id=ctx.session_id, candidates=proposing["candidates"])
            return {"affect_labeling": proposing}

        # ② 上轮已 proposing：归类用户当前回应
        if al.get("status") == "proposing":
            candidates = list(al.get("candidates") or [])
            out = await self._classify(ctx, candidates)
            kind = out.get("kind", "no_answer")
            if kind in ("chosen", "custom"):
                return {"affect_labeling": self._close(
                    al, now_seq, reason=_CLOSED_REASON_ANSWERED, outcome={
                        "emotion_label": out.get("label"),
                        "user_confirmed": bool(out.get("user_confirmed", False)),
                        "user_correction": bool(out.get("user_correction", False)),
                        "source": "chosen" if kind == "chosen" else "custom",
                        "confidence": out.get("confidence", 0.0),
                    },
                )}
            if kind == "unknown":
                return {"affect_labeling": self._close(
                    al, now_seq, reason=_CLOSED_REASON_ANSWERED, outcome={
                        "emotion_label": None,
                        "user_confirmed": False,
                        "user_correction": False,
                        "source": "unknown",
                        "confidence": out.get("confidence", 0.0),
                    },
                )}
            if kind == "refused":
                return {"affect_labeling": self._close(
                    al, now_seq, reason=_CLOSED_REASON_ANSWERED, outcome={
                        "emotion_label": None,
                        "user_confirmed": False,
                        "user_correction": False,
                        "source": "refused",
                        "confidence": out.get("confidence", 0.0),
                    },
                )}
            # no_answer：用户这轮没接那个问题，仍在讲——本回复已由对话线轻带一次候选，
            # 到此收尾（不第三次追问），close 回普通对话。
            return {"affect_labeling": self._close(
                al, now_seq, reason=_CLOSED_REASON_NO_RESPONSE, outcome={
                    "emotion_label": None,
                    "user_confirmed": False,
                    "user_correction": False,
                    "source": None,
                    "confidence": 0.0,
                },
            )}

        return {}

    # ── 内部工具 ─────────────────────────────────────────────────────

    async def _classify(self, ctx: TurnContext, candidates: list) -> dict:
        skill = self._response_skill()
        if skill is None:
            return {"kind": "no_answer"}
        bound = self._llm.bind(
            agent="affect_labeling_response",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        try:
            result = await skill.aexecute(
                {"user_text": ctx.message, "candidates": candidates},
                {"llm": bound},
            )
        except Exception as e:
            app_log("warning", "affect", "response_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return {"kind": "no_answer"}
        if not result.success:
            return {"kind": "no_answer"}
        return result.output

    @staticmethod
    def _close(base: dict, now_seq: int, *, reason: str, outcome: dict) -> dict:
        """把一条 open 记录合成为 closed 记录（保留 candidates 便于追溯）。"""
        closed = {
            "status": "closed",
            "candidates": list(base.get("candidates") or []),
            "start_seq": base.get("start_seq"),
            "closed_seq": now_seq,
            "close_reason": reason,
            "outcome": outcome,
        }
        if base.get("reason"):
            closed["reason"] = base["reason"]
        return closed
