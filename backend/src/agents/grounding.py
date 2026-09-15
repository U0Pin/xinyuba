"""Grounding（着陆/回到当下）决策引擎（新架构，决策线可选依赖）。

定位：非疗法 Emotion Support，与 PMR / Affect Labeling 同属 EMOTION_SUPPORT，
**互斥、优先级介于 PMR 与 Affect 之间**（CRISIS > SAFETY > 当前疗法 > PMR >
Grounding > Affect Labeling > DAILY）。

产品定位（spec §2/§8/§9，严格遵守）：
- 唯一目标：帮用户从过度卷入/失控/脱离当下的状态暂时回到「此时此地」；
- 一轮只 3 个动作（视觉锚定 → 触觉/身体接触锚定 → 声音/位置确认）+ check 收尾，
  全程约 1–3 分钟；不进 5-4-3-2-1 完整版、不调呼吸、不观念头、不命名情绪；
- 用户可随时拒绝/退出/说好转了；重复提议被冷却与状态机双重抑制。

两阶段职责分离（单写者纪律，与 PMR/Affect 完全同一约定）：
- `step(ctx)`：本轮回复开始前被 Scheduler 串行调用。🔴 对 `ctx.state` **零副作用**——
  只读上一轮 `state.grounding` 与本轮消息，构造**新的** grounding_turn dict；
  一切持久化只能经 `run()`（回传 state_updates）或 signals.apply() 轮边界完成。
- `run(ctx, *, switched_to_therapy)`：决策末尾被 ESO 调用，**无 LLM**，把 step()
  算好的 `_next_state` 落盘；切疗法/危机让位时返回清残留。

LLM 预算：gate miss / 纯代码可判的 accept·stop·exit·feeling-better 一律 0 调用；
仅「空闲 gate 命中 → offer」+1 `grounding_opportunity`、练习中模棱两可
+1 `grounding_response`。run() 永远 0 调用。
"""

from __future__ import annotations

from typing import Optional

from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.core.scheduler import TurnContext
from src.core.skill import skill_registry
from src.core.state import RiskState
from src.skills.grounding_lexicon import (
    ACCEPT_CUES,
    CRISIS_CUES,
    NEG_PREFIXES,
    STOP_CUES,
)
from src.store.session_store import SessionStore
import src.skills  # noqa: F401 — 确保技能已注册（副作用 import）

# 关闭/退出原因
_REASON_DECLINED = "declined"
_REASON_DISCOMFORT = "discomfort"
_REASON_EXITED = "exited"
_REASON_FELT_BETTER = "felt_better"
_REASON_SUPERSEDED = "superseded"
_REASON_COMPLETED = "completed"
_REASON_TOPIC_SHIFT = "topic_shift"

# 引导步骤（state.step）：0 visual / 1 tactile / 2 auditory+place / 3 check
_STEP_VISUAL, _STEP_TACTILE, _STEP_AUDITORY, _STEP_CHECK = 0, 1, 2, 3

# 关闭后冷却（以流水条数计，与 PMR/affect 同一量级，约 3 轮对话）
_COOLDOWN_SEQS = 6

# 状态里需要随每次推进复制的键
_STATE_KEYS = ("status", "step", "trigger_seq", "last_seq", "close_reason")


def _short_text(text: str) -> str:
    return (text or "").strip()


class GroundingEngine:
    """把 Grounding 的机会评估 / 回应理解 / 三步引导翻译成调度状态的最小引擎。"""

    cooldown_seqs: int = _COOLDOWN_SEQS

    def __init__(
        self,
        llm: LLMClient,
        session_store: Optional[SessionStore] = None,
        cooldown_seqs: int = _COOLDOWN_SEQS,
    ):
        self._llm = llm
        self.sessions = session_store or SessionStore()
        self.cooldown_seqs = cooldown_seqs

    # ── 技能句柄（惰性取） ──────────────────────────────────────────

    def _opportunity_skill(self):
        return skill_registry.get("grounding_opportunity")

    def _response_skill(self):
        return skill_registry.get("grounding_response")

    # ═══════════════════════════════════════════════════════════════════════
    # gate（纯代码、零 LLM 预筛；hard exclusion 与既有引擎同一约定）
    # ═══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _has(text: str, cues) -> bool:
        return any(c in text for c in cues)

    def _gate(self, text: str) -> bool:
        t = _short_text(text)
        if not t:
            return False
        if self._has(t, CRISIS_CUES):
            return False  # 危机让位安全流程（最高优先 hard exclusion）
        from src.skills.grounding_lexicon import (
            DETACHMENT_CUES,
            EXTERNAL_ANCHOR_REQUEST_CUES,
            OVERWHELM_CUES,
            RUMINATION_CUES,
            STABILIZE_REQUEST_CUES,
        )
        return (
            self._has(t, STABILIZE_REQUEST_CUES)
            or self._has(t, EXTERNAL_ANCHOR_REQUEST_CUES)
            or self._has(t, OVERWHELM_CUES)
            or self._has(t, DETACHMENT_CUES)
            or self._has(t, RUMINATION_CUES)
        )

    def _closed_recent(self, ctx: TurnContext) -> bool:
        g = ctx.state.grounding
        if not g or g.get("status") != "closed":
            return False
        closed_seq = int(g.get("last_seq") or 0)
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        return closed_seq > 0 and (now_seq - closed_seq) <= self.cooldown_seqs

    # ═══════════════════════════════════════════════════════════════════════
    # step：对话前的本轮判定（对 ctx.state 零副作用）
    # ═══════════════════════════════════════════════════════════════════════

    async def step(self, ctx: TurnContext) -> Optional[dict]:
        """返回本轮要说的 grounding_turn（含 `_next_state`）；无事可做返回 None。

        只读 `ctx.state.grounding` 与本轮消息；**绝不写 `ctx.state`**。
        """
        state = ctx.state
        if state.crisis or state.therapy is not None:
            return None
        if state.risk_level in (RiskState.HIGH_RISK, RiskState.CRISIS):
            return None  # 高危暂停：不推进（清残留由决策端/signal 负责）
        if self._has(ctx.message, CRISIS_CUES):
            return None  # 本条消息含危机信号：交给安全流程，一律不提议/不推进
        g = state.grounding
        status = g.get("status") if isinstance(g, dict) else None
        if status == "proposing":
            return await self._handle_proposing(ctx, g)
        if status == "active":
            return await self._handle_active(ctx, g)
        # off / closed（冷却内拒绝，冷却过后可再提）
        return await self._handle_off(ctx)

    # ── off：机会评估（同轮提议，命中才 +1 opportunity LLM） ────────
    async def _handle_off(self, ctx: TurnContext) -> Optional[dict]:
        if not self._gate(ctx.message):
            return None
        if self._closed_recent(ctx):
            return None
        skill = self._opportunity_skill()
        if skill is None:
            return None
        bound = self._llm.bind(
            agent="grounding_opportunity",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        from src.utils.text import history_pairs
        tail = history_pairs(self.sessions.read_flow_tail(ctx.session_id, 8))
        try:
            result = await skill.aexecute(
                {
                    "user_text": ctx.message,
                    "history": tail,
                    "profile": ctx.profile or {},
                },
                {"llm": bound},
            )
        except Exception as e:  # noqa: BLE001 — 机会评估失败退回普通回复
            app_log("warning", "grounding", "opportunity_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return None
        if not result.success or not result.output.get("should_offer"):
            return None
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        proposing = {
            "status": "proposing",
            "step": 0,
            "trigger_seq": now_seq,
            "last_seq": now_seq,
            "close_reason": None,
        }
        return {
            "kind": "offer",
            "line": self._offer_line(),
            "_next_state": proposing,
        }

    def _offer_line(self) -> str:
        return ("听起来你现在有点乱……要不要先做个很小的「回到当下」练习？"
                "就一分钟：先看看你周围，找一样你能清楚看到的东西。随时可以停。")

    # ── proposing：等接受/拒绝 ──────────────────────────────────────

    async def _handle_proposing(self, ctx: TurnContext, g: dict) -> Optional[dict]:
        text = _short_text(ctx.message)
        if self._has(text, CRISIS_CUES):
            return None
        # ① 明确拒绝/退出/已好转（纯代码，0 LLM）
        if self._has(text, STOP_CUES) or self._short_refusal(text):
            return self._close_turn(ctx, g, reason=_REASON_DECLINED)
        if self._has(text, ("好多", "好些了", "好点了", "缓过来了", "稳了")):
            return self._close_turn(ctx, g, reason=_REASON_FELT_BETTER)
        if self._accept_short(text):
            return self._accept_start_turn(ctx, g)
        # ② 模棱两可 → 1 次 response classifier
        out = await self._classify(ctx, g, step_label="对一次「要不要回到当下」邀请的回应（还没开始）")
        kind = out.get("kind", "other")
        if kind in ("affirmative", "other"):
            return self._accept_start_turn(ctx, g)
        if kind == "stopped_feeling_better":
            return self._close_turn(ctx, g, reason=_REASON_FELT_BETTER)
        if kind == "discomfort":
            return self._close_turn(ctx, g, reason=_REASON_DISCOMFORT)
        if kind == "topic_shift":
            return self._close_turn(ctx, g, reason=_REASON_TOPIC_SHIFT)
        # negative / pause / 分类失败 → 不开始（温和收回，不算中断关系）
        return self._close_turn(ctx, g, reason=_REASON_DECLINED)

    @staticmethod
    def _short_refusal(text: str) -> bool:
        t = _short_text(text)
        return bool(t) and len(t) <= 8 and t.startswith(NEG_PREFIXES)

    def _accept_short(self, text: str) -> bool:
        t = _short_text(text)
        if not t:
            return False
        if self._has(t, STOP_CUES) or t.startswith(NEG_PREFIXES):
            return False
        return len(t) <= 8 and self._has(t, ACCEPT_CUES)

    def _accept_start_turn(self, ctx: TurnContext, g: dict) -> dict:
        """接受后**同一轮**返回第一步引导（视觉锚定）；run() 落盘 active/step=0。"""
        next_state = self._derive(g, status="active", step=_STEP_VISUAL, close_reason=None)
        return {
            "kind": "guide",
            "step": _STEP_VISUAL,
            "step_name": "visual",
            "line": self._step_line(_STEP_VISUAL),
            "_next_state": next_state,
        }

    # ── active：逐步推进 / 退出（纯代码优先，模糊才 classifier） ─────

    async def _handle_active(self, ctx: TurnContext, g: dict) -> Optional[dict]:
        text = _short_text(ctx.message)
        if self._has(text, CRISIS_CUES):
            return None  # 交给安全线，不推进不清
        step = int(g.get("step") or 0)
        # ① check 步：用户回应即完成（本回复已收尾，不追问强度/内容）
        if step >= _STEP_CHECK:
            return self._closing_turn(g)
        step_label = f"在「回到当下」练习的第 {step + 1} 步（{self._step_name(step)}）引导中"
        # ① 明确退出 / 已好转（纯代码，0 LLM）
        if self._has(text, STOP_CUES) or self._short_refusal(text):
            return self._close_turn(ctx, g, reason=_REASON_EXITED)
        if self._has(text, ("我好多了", "好多了", "好些了", "缓过来了", "稳了", "不乱了")):
            return self._close_turn(ctx, g, reason=_REASON_FELT_BETTER)
        # ② 短软确认 → 纯代码进下一步；③ 模糊 → classifier（分类失败默认继续）
        lead = self._lead_sentence(text)
        if self._soft_continue(lead):
            return self._advance_turn(ctx, g)
        out = await self._classify(ctx, g, step_label=step_label)
        kind = out.get("kind", "other")
        if kind in ("affirmative", "other"):
            return self._advance_turn(ctx, g)
        if kind == "stopped_feeling_better":
            return self._close_turn(ctx, g, reason=_REASON_FELT_BETTER)
        if kind == "discomfort":
            return self._close_turn(ctx, g, reason=_REASON_DISCOMFORT)
        if kind == "topic_shift":
            return self._close_turn(ctx, g, reason=_REASON_TOPIC_SHIFT)
        # negative / pause / 分类失败 → 温和收尾退出（不卡死，也不硬拉）
        return self._close_turn(ctx, g, reason=_REASON_EXITED)

    @staticmethod
    def _lead_sentence(text: str) -> str:
        import re
        t = _short_text(text)
        chunk = re.split(r"[。！？!?；;]", t, maxsplit=1)[0]
        return chunk.strip() or t

    def _soft_continue(self, lead: str) -> bool:
        if not lead or len(lead) > 10:
            return False
        return self._has(lead, ("嗯", "好", "可以", "继续", "いい", "嗯嗯",
                                "看到了", "找到了", "感觉到了", "听见了", "听到了", "做了"))

    def _advance_turn(self, ctx: TurnContext, g: dict) -> dict:
        """推进到下一步（或 check → complete）。"""
        step = int(g.get("step") or 0)
        next_step = step + 1
        if next_step <= _STEP_AUDITORY:
            next_state = self._derive(g, step=next_step)
            return {
                "kind": "guide",
                "step": next_step,
                "step_name": self._step_name(next_step),
                "line": self._step_line(next_step),
                "_next_state": next_state,
            }
        # check 步：问一句简短确认（下一轮回复即结束）
        next_state = self._derive(g, step=_STEP_CHECK)
        return {
            "kind": "check",
            "step": _STEP_CHECK,
            "step_name": "check",
            "line": self._check_line(),
            "_next_state": next_state,
        }

    def _closing_turn(self, g: dict) -> dict:
        next_state = self._derive(g, status="closed", close_reason=_REASON_COMPLETED)
        return {
            "kind": "closing_feedback",
            "line": None,
            "_next_state": next_state,
        }

    # ── 关闭（构造新 dict，绝不原地改 ctx.state） ──────────────────

    def _close_turn(self, ctx: TurnContext, g: dict, *, reason: str) -> dict:
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        closed = self._derive(g, status="closed", close_reason=reason, last_seq=now_seq)
        return {"kind": "ack_close", "line": None, "reason": reason, "_next_state": closed}

    # ═══════════════════════════════════════════════════════════════════════
    # response classifier（回应模棱两可时的 1 次轻量 LLM）
    # ═══════════════════════════════════════════════════════════════════════

    async def _classify(self, ctx: TurnContext, g: dict, *, step_label: str) -> dict:
        skill = self._response_skill()
        if skill is None:
            return {"kind": "other", "reason": "no_skill"}
        bound = self._llm.bind(
            agent="grounding_response",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        try:
            result = await skill.aexecute(
                {"user_text": ctx.message, "step_label": step_label},
                {"llm": bound},
            )
        except Exception as e:  # noqa: BLE001 — 分类失败由调用方降级
            app_log("warning", "grounding", "response_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return {"kind": "other", "reason": "response_llm_failed"}
        if not result.success:
            return {"kind": "other", "reason": "response_failed"}
        return result.output

    # ═══════════════════════════════════════════════════════════════════════
    # 决策末尾落盘（ESO 独占写 state 的通道之一；无 LLM）
    # ═══════════════════════════════════════════════════════════════════════

    async def run(self, ctx: TurnContext, *, switched_to_therapy: bool) -> dict:
        """返回要并入 state 的 grounding 更新；无变化返回 {}。step() 已算好 _next_state。"""
        if switched_to_therapy:
            if ctx.state.grounding:
                return {"grounding": None}  # 让位当前疗法流程（signals 亦会清）
            return {}
        turn = ctx.grounding_turn
        if turn is not None and isinstance(turn.get("_next_state"), dict):
            return {"grounding": turn["_next_state"]}
        return {}

    # ── 内部工具 ─────────────────────────────────────────────────────

    @staticmethod
    def _step_name(step: int) -> str:
        return {0: "visual", 1: "tactile", 2: "auditory", 3: "check"}.get(step, "check")

    @staticmethod
    def _derive(g: dict, **overrides) -> dict:
        """基于上轮记录构造新状态 dict（副本，绝不原地改 ctx.state 快照）。"""
        base = {k: g.get(k) for k in _STATE_KEYS}
        base.update(overrides)
        return base

    # ── 引导话术（对话层措辞；Host 只做表述层软化） ─────────────────

    def _step_line(self, step: int) -> str:
        if step == _STEP_VISUAL:
            return ("先不用想刚才的事。看看你现在周围，找一样你能清楚看到的东西——"
                    "随便什么都行：一个杯子、一扇窗、一盏灯。找到了就在心里记一下它的样子。")
        if step == _STEP_TACTILE:
            return ("接下来，感受一下身体和外界的接触——"
                    "脚踩在地面上的感觉，或者手和桌子、椅子、手机的接触。"
                    "不用找词形容，就留意那种「碰到」的感觉就好。")
        if step == _STEP_AUDITORY:
            return ("再听一下周围的声音——不用很安静的地方也行，"
                    "任何一种声音都可以。听到的同时，"
                    "在心里确认一下：你现在在哪里？大概是什么时候？")
        return ""

    def _check_line(self) -> str:
        return ("好——现在感觉怎么样？有没有比刚才稍微落地一点？不用勉强说好或不好，"
                "怎么样都行。")
