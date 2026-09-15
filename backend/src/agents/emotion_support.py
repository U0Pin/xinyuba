"""Emotion Support Orchestrator (ESO) —— 薄、确定性的情绪支持编排器。

定位：非疗法 Emotion Support 的统一调度点。协调已装配的 Emotion Support Skill
（当前：affect_labeling、progressive_muscle_relaxation）的 eligibility / active-skill /
candidate selection / priority / mutual exclusion / continuation / supersede / lifecycle。

职责边界（严格遵守）：
- 只做「现在能不能做 / 做哪个 / 要不要继续 / 是否被抢占」的确定性编排；
- 不负责危机 / 安全 / 疗法检测（消费上层已有信号）；不做情绪识别 / 诊断；
- 不做 LLM 编排；不触碰任何 Therapy 核心逻辑；
- Skill 内部状态机（pmr 的 prepare/tension/release/notice；affect 的 proposing/closed）
  仍由各自 engine 维护，ESO 不越界。

全局优先级（严格保持）：
    CRISIS > SAFETY > CURRENT THERAPY > PMR > AFFECT LABELING > DAILY
每轮最多一个 Emotion Support Skill active。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Optional

from src.core.logging_utils import app_log
from src.core.state import RiskState, SessionState
from src.store.session_store import SessionStore

if TYPE_CHECKING:
    from src.core.scheduler import TurnContext


class EmotionSupportAction(str, Enum):
    """编排层动作。complete/decline/exit 由 skill 内部状态机表达，不在此枚举。"""

    NONE = "none"
    START = "start"
    CONTINUE = "continue"
    SUPERSEDE = "supersede"


@dataclass(frozen=True)
class EmotionSupportSkillSpec:
    """Skill 的统一元数据：优先级（数值越大越优先）+ 是否互斥。"""

    name: str
    priority: int
    exclusive: bool


@dataclass(frozen=True)
class EmotionSupportDecision:
    """ESO 一轮编排决策（确定性、可观察，供日志 / 评测）。"""

    action: EmotionSupportAction
    skill: Optional[str]
    reason: str


class EmotionSupportOrchestrator:
    """协调已装配 Emotion Support Skill 的薄编排器（无 LLM）。"""

    # 统一 skill 元数据（未来再加 skill 只需追加一条 spec）
    SPECS: tuple[EmotionSupportSkillSpec, ...] = (
        EmotionSupportSkillSpec(name="pmr", priority=20, exclusive=True),
        EmotionSupportSkillSpec(name="grounding", priority=15, exclusive=True),
        EmotionSupportSkillSpec(name="affect_labeling", priority=10, exclusive=True),
    )

    def __init__(
        self,
        *,
        pmr_engine=None,
        affect_engine=None,
        grounding_engine=None,
        session_store: Optional[SessionStore] = None,
    ):
        self.pmr_engine = pmr_engine
        self.affect_engine = affect_engine
        self.grounding_engine = grounding_engine
        self.sessions = session_store or SessionStore()

    # ── 生命周期探测（纯代码） ───────────────────────────────────────

    @staticmethod
    def pmr_is_live(state: SessionState) -> bool:
        """state.pmr 是否处于进行中的 PMR（off/closed 之外）。"""
        pmr = state.pmr
        return bool(pmr) and pmr.get("status") in ("proposing", "active")

    @staticmethod
    def affect_is_live(state: SessionState) -> bool:
        """state.affect_labeling 是否处于提问待回应中（proposing）。"""
        al = state.affect_labeling
        return bool(al) and al.get("status") == "proposing"

    @staticmethod
    def grounding_is_live(state: SessionState) -> bool:
        """state.grounding 是否处于进行中的 Grounding（off/closed 之外）。"""
        g = state.grounding
        return bool(g) and g.get("status") in ("proposing", "active")

    @classmethod
    def active_skill(cls, state: SessionState) -> Optional[str]:
        """当前进行中的 emotion support skill（用于 continuation 优先）。"""
        if cls.pmr_is_live(state):
            return "pmr"
        if cls.grounding_is_live(state):
            return "grounding"
        if cls.affect_is_live(state):
            return "affect_labeling"
        return None

    # ── eligibility（纯代码） ────────────────────────────────────────

    @staticmethod
    def eligible(state: SessionState) -> bool:
        """日常态 + 无危机 + 无疗程 才允许 emotion support。"""
        return (not state.crisis and state.therapy is None and state.owner == "daily")

    # ── plan：对话前（探测候选 + 写回 ctx） ─────────────────────────

    async def plan(self, ctx: "TurnContext") -> EmotionSupportDecision:
        """决定本轮对话线带出哪个 skill 的引导块（同轮提问时序）。

        副作用：把 engine 产出写回 ctx.pmr_turn / ctx.affect_labeling_plan，
        供对话线在**本轮回复**里带出引导块。不写 state（单写者纪律）。
        """
        if not self.eligible(ctx.state):
            return EmotionSupportDecision(
                EmotionSupportAction.NONE, None, "blocked_not_daily")

        # PMR 优先探测
        if self.pmr_engine is not None:
            try:
                ctx.pmr_turn = await self.pmr_engine.step(ctx)
            except Exception as e:  # noqa: BLE001 — 预取失败退回普通回复
                app_log("warning", "es_orchestrator", "pmr_step_failed",
                        trace_id=ctx.trace_id, error=str(e))
                ctx.pmr_turn = None

        pmr_live = ctx.pmr_turn is not None or self.pmr_is_live(ctx.state)
        if pmr_live:
            if ctx.pmr_turn is not None:
                action = (EmotionSupportAction.START
                          if ctx.pmr_turn.get("kind") == "offer"
                          else EmotionSupportAction.CONTINUE)
                return EmotionSupportDecision(action, "pmr", "pmr_turn")
            return EmotionSupportDecision(
                EmotionSupportAction.CONTINUE, "pmr", "pmr_active")

        # PMR 不占槽 → 探 Grounding（次优先）
        if self.grounding_engine is not None:
            try:
                ctx.grounding_turn = await self.grounding_engine.step(ctx)
            except Exception as e:  # noqa: BLE001 — 预取失败退回普通回复
                app_log("warning", "es_orchestrator", "grounding_step_failed",
                        trace_id=ctx.trace_id, error=str(e))
                ctx.grounding_turn = None

        grounding_live = ctx.grounding_turn is not None or self.grounding_is_live(ctx.state)
        if grounding_live:
            if ctx.grounding_turn is not None:
                action = (EmotionSupportAction.START
                          if ctx.grounding_turn.get("kind") in ("offer", "guide")
                          else EmotionSupportAction.CONTINUE)
                return EmotionSupportDecision(action, "grounding", "grounding_turn")
            return EmotionSupportDecision(
                EmotionSupportAction.CONTINUE, "grounding", "grounding_active")

        # PMR / Grounding 都不占槽 → 探 Affect
        if self.affect_engine is not None:
            try:
                ctx.affect_labeling_plan = await self.affect_engine.maybe_plan(ctx)
            except Exception as e:  # noqa: BLE001
                app_log("warning", "es_orchestrator", "affect_maybe_plan_failed",
                        trace_id=ctx.trace_id, error=str(e))
                ctx.affect_labeling_plan = None

        if ctx.affect_labeling_plan is not None:
            return EmotionSupportDecision(
                EmotionSupportAction.START, "affect_labeling", "affect_candidate")
        return EmotionSupportDecision(EmotionSupportAction.NONE, None, "no_candidate")

    # ── run：决策后（落盘） ─────────────────────────────────────────

    def _decide_run(
        self,
        ctx: "TurnContext",
        *,
        risk: RiskState,
    ) -> EmotionSupportDecision:
        """决策后编排决策（纯、确定）：本轮让哪个 skill 落盘 / 是否被抢占。"""
        # PMR 占据情绪支持槽（本轮有产出，或 state.pmr 进行中）
        if self.pmr_engine is not None and (ctx.pmr_turn is not None or self.pmr_is_live(ctx.state)):
            if risk in (RiskState.HIGH_RISK, RiskState.CRISIS) or ctx.state.therapy is not None:
                return EmotionSupportDecision(
                    EmotionSupportAction.SUPERSEDE, "pmr", "blocked_by_higher_priority")
            return EmotionSupportDecision(
                EmotionSupportAction.CONTINUE, "pmr", "pmr_live")
        # Grounding 次优先：本轮有产出，或 state.grounding 进行中
        if (self.grounding_engine is not None
                and (ctx.grounding_turn is not None or self.grounding_is_live(ctx.state))):
            if risk in (RiskState.HIGH_RISK, RiskState.CRISIS) or ctx.state.therapy is not None:
                return EmotionSupportDecision(
                    EmotionSupportAction.SUPERSEDE,
                    "grounding", "blocked_by_higher_priority")
            return EmotionSupportDecision(
                EmotionSupportAction.CONTINUE, "grounding", "grounding_live")
        # 否则走 Affect
        if self.affect_engine is None:
            return EmotionSupportDecision(EmotionSupportAction.NONE, None, "no_affect_engine")
        if risk in (RiskState.HIGH_RISK, RiskState.CRISIS) or ctx.state.therapy is not None:
            return EmotionSupportDecision(
                EmotionSupportAction.SUPERSEDE,
                self.active_skill(ctx.state) or "affect_labeling",
                "blocked_by_higher_priority")
        return EmotionSupportDecision(
            EmotionSupportAction.CONTINUE, "affect_labeling", "affect_run")

    async def run(
        self,
        ctx: "TurnContext",
        *,
        switched_to_therapy: bool,
        risk: RiskState,
    ) -> dict:
        """返回要并入 state 的更新（无变化返回 {}）。等价原 DecisionLine._emotion_updates。"""
        decision = self._decide_run(ctx, risk=risk)
        if decision.action == EmotionSupportAction.CONTINUE:
            if decision.skill == "pmr":
                return await self._run_pmr(ctx, switched_to_therapy)
            if decision.skill == "grounding":
                return await self._run_grounding(ctx, switched_to_therapy)
            return await self._run_affect(ctx, switched_to_therapy)
        return {}

    async def _run_pmr(self, ctx: "TurnContext", switched_to_therapy: bool) -> dict:
        updates: dict = {}
        try:
            updates.update(await self.pmr_engine.run(ctx, switched_to_therapy=switched_to_therapy))
        except Exception as e:  # noqa: BLE001 — PMR 失败不阻塞主流程
            app_log("warning", "es_orchestrator", "pmr_run_failed",
                    trace_id=ctx.trace_id, error=str(e))
        # PMR 占据情绪支持槽：残留 Affect/Grounding 提问被 supersede（PMR 优先级更高）
        if ctx.state.affect_labeling is not None:
            updates["affect_labeling"] = None
        if ctx.state.grounding is not None:
            updates["grounding"] = None
        return updates

    async def _run_grounding(self, ctx: "TurnContext", switched_to_therapy: bool) -> dict:
        updates: dict = {}
        try:
            updates.update(await self.grounding_engine.run(ctx, switched_to_therapy=switched_to_therapy))
        except Exception as e:  # noqa: BLE001 — Grounding 失败不阻塞主流程
            app_log("warning", "es_orchestrator", "grounding_run_failed",
                    trace_id=ctx.trace_id, error=str(e))
        # Grounding 占据情绪支持槽：残留 Affect 提问被 supersede（Grounding 优先级更高）
        if ctx.state.affect_labeling is not None:
            updates["affect_labeling"] = None
        return updates

    async def _run_affect(self, ctx: "TurnContext", switched_to_therapy: bool) -> dict:
        try:
            return await self.affect_engine.run(ctx, switched_to_therapy=switched_to_therapy)
        except Exception as e:  # noqa: BLE001
            app_log("warning", "es_orchestrator", "affect_run_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return {}
