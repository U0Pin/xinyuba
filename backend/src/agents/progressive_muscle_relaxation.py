"""PMR（渐进式肌肉放松）决策引擎（新架构，决策线可选依赖）。

定位：非疗法 Emotion Support，与 Affect Labeling 同属 EMOTION_SUPPORT 但**互斥、
PMR 优先级更高**（CRISIS > SAFETY > 当前疗法 > PMR > Affect Labeling > DAILY）。
本引擎只在「daily 无 crisis 无 therapy 且 risk 非高危」的轮次被 Scheduler 调用
（优先级与互斥在 Scheduler / DecisionLine 显式编码，engine 内部再自守一道）。

产品节奏（需求澄清确认）：每个身体区域 2 个 conversational beats——
- Beat1 = prepare + tension（对话层合并，一次落盘原子推进）；
- Beat2 = release + notice。
内部状态机仍单调推进，但相邻子相不在对话里逐相确认；每拍结束后**软确认**，
按自然语言反馈判断 继续/暂停/退出/切话题/不适，不机械等「好了」。

两阶段职责分离（单写者纪律，严禁越界）：
- `step(ctx)`：在**本轮回复开始前**被 Scheduler 串行调用，先于对话判好本拍要说什么。
  🔴 对 `ctx.state` **零副作用**——只读取上一轮 `state.pmr` 与本轮消息，构造**新的**
  pmr_turn dict；一切状态持久化只能经 `run()`（回传 state_updates）或既有
  signals.apply() 路径在轮边界完成。**禁止在 step() 里原地修改 ctx.state.pmr 或任何
  共享快照**（ctx 是与并行对话线共享的只读快照，见 ARCHITECTURE「单写者纪律」）。
- `run(ctx, *, switched_to_therapy)`：决策末尾被 DecisionLine 调用，**无 LLM**，把
  step() 算好的 `_next_state` 落盘（切疗法/抢占 affect 的清残留由 DecisionLine 负责）。

LLM 预算：gate miss / 纯代码可判的 accept·continue·stop·discomfort 一律 0 调用；
仅「空闲 gate 命中 → offer」+1 `pmr_opportunity`、「proposing/active 回复模棱两可」
+1 `pmr_response`。run() 永远 0 调用。
"""

from __future__ import annotations

import re
from typing import Optional

from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.core.scheduler import TurnContext
from src.core.skill import skill_registry
from src.core.state import RiskState
from src.skills.pmr import PMR_BODY_PARTS
from src.skills.pmr_lexicon import (
    ACCEPT_CUES,
    BODY_REGION_ANCHORS,
    BODY_TENSION_CUES,
    BODY_TENSION_MARKERS,
    CHEST_CARDIAC_CUES,
    CRISIS_CUES,
    DISCOMFORT_CUES,
    IMAGINAL_CUES,
    NEG_PREFIXES,
    NEW_TOPIC_LEAVE_CUES,
    RELAXATION_REQUEST_CUES,
    TENSION_RELEASE_MARKERS,
    SHORT_SOFT_CONTINUE_CUES,
    STOP_CUES,
)
from src.store.session_store import SessionStore
from src.utils.text import history_pairs

import src.skills  # noqa: F401 — 确保技能已注册（副作用 import）

# 关闭/退出原因（决定 5）
_REASON_DECLINED = "declined"
_REASON_DISCOMFORT = "discomfort"
_REASON_STOPPED = "stopped"
_REASON_TOPIC_SHIFT = "topic_shift"
_REASON_COMPLETED = "completed"

# 关闭后冷却（以流水条数计，与 affect 同一量级，约 3 轮对话），避免反复打扰
_COOLDOWN_SEQS = 6

# 拒绝前缀：短句以这些开头时不算接受（配合 ACCEPT 短句判定，拦截「不行/不要/还算了吧」）
# NEG_PREFIXES imported above.

# 引导句子里避免误判不适的良性表达（「痛快」是舒服不是疼；「麻烦」不是麻）
_PLEASANT_EXCLUSIONS = ("痛快", "麻烦")

_LEAD_SPLIT = re.compile(r"[。！？!?；;]")

# 状态里需要随每次推进复制的键
_STATE_KEYS = (
    "status", "mode", "start_seq", "body_parts", "part_index", "current_part",
    "phase", "beats_done", "parts_completed", "closing",
)


def _lead_sentence(text: str) -> str:
    """取第一个句子（按 。！？； 等断句），用于「是否先回应了本拍」的判定。"""
    t = (text or "").strip()
    chunk = _LEAD_SPLIT.split(t, maxsplit=1)[0]
    return chunk.strip() or t


class ProgressiveMuscleRelaxationEngine:
    """把 PMR 的机会评估 / 回应理解 / 拍推进翻译成调度状态的最小引擎。"""

    # 类级默认，保证 __new__ 直造（纯代码单测）也能安全读冷却配置。
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

    # ── 技能句柄（注册可能晚于模块 import，惰性取） ─────────────────

    def _opportunity_skill(self):
        return skill_registry.get("pmr_opportunity")

    def _response_skill(self):
        return skill_registry.get("pmr_response")

    # ═══════════════════════════════════════════════════════════════════════════
    # 触发预筛 gate（纯代码、零 LLM；只决定“是否值得跑一次机会 LLM”，非诊断）
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _has(text: str, cues) -> bool:
        return any(c in text for c in cues)

    def _gate(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if self._has(t, CRISIS_CUES):
            return False  # 危机让位安全流程（最高优先 hard exclusion）
        if self._has(t, CHEST_CARDIAC_CUES):
            return False  # 心肺体感：PMR 不碰，也不作紧绷线索（hard exclusion）
        # 身体紧绷 / 想放松 → 候选；是否真正提议交给机会 LLM。
        # 泛化形态（「肩膀好紧」「背很硬」）用「身体区域锚点 + 单字紧绷标记」组合覆盖；
        # anchors 不含 心/胸/肺 → 结构与情绪化(心里紧) / 心肺体感(胸口紧) 天然隔离。
        if (self._has(t, BODY_TENSION_CUES)
                or self._has(t, RELAXATION_REQUEST_CUES)
                or (self._has(t, BODY_TENSION_MARKERS) and self._has(t, BODY_REGION_ANCHORS))
                or (self._has(t, TENSION_RELEASE_MARKERS) and self._has(t, BODY_REGION_ANCHORS))):
            return True
        return False

    def _closed_recent(self, ctx: TurnContext) -> bool:
        pmr = ctx.state.pmr
        if not pmr or pmr.get("status") != "closed":
            return False
        closed_seq = int(pmr.get("closed_seq") or 0)
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        return closed_seq > 0 and (now_seq - closed_seq) <= self.cooldown_seqs

    # ═══════════════════════════════════════════════════════════════════════════
    # step：对话前的本拍判定（对 ctx.state 零副作用——见模块 docstring 硬约束）
    # ═══════════════════════════════════════════════════════════════════════════

    async def step(self, ctx: TurnContext) -> Optional[dict]:
        """返回本轮要说的 pmr_turn（含 `_next_state`）；无事可做返回 None。

        只读 `ctx.state.pmr` 与本轮消息；**绝不写 `ctx.state`**。
        """
        state = ctx.state
        if state.crisis or state.therapy is not None:
            return None
        if state.risk_level in (RiskState.HIGH_RISK, RiskState.CRISIS):
            return None  # 高危暂停：不推进（由决策端负责高危清残留）
        if self._has(ctx.message, CRISIS_CUES):
            return None  # 本条消息含危机信号：交给安全流程，一律不推进/不判拍
        pmr = state.pmr
        status = pmr.get("status") if isinstance(pmr, dict) else None
        if status == "proposing":
            return await self._handle_proposing(ctx, pmr)
        if status == "active":
            if pmr.get("closing"):
                return self._closing_feedback_turn(ctx, pmr)
            return await self._handle_active(ctx, pmr)
        # 其余一律走 off：无记录，或 closed（冷却内 _handle_off 会拒绝，冷却过后可再提）
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
            agent="pmr_opportunity",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
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
            app_log("warning", "pmr", "opportunity_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return None
        if not result.success or not result.output.get("should_offer"):
            return None
        focus = result.output.get("body_focus") or "general"
        order = self._order_parts(focus)
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        region = order[0]
        proposing = {
            "status": "proposing",
            "mode": "gentle",
            "start_seq": now_seq,
            "body_parts": list(order),
            "part_index": 0,
            "current_part": region,
            "phase": "prepare",
            "beats_done": 0,
            "parts_completed": 0,
            "closing": False,
        }
        focus_label = PMR_BODY_PARTS.get(focus) if focus in PMR_BODY_PARTS else None
        line = self._offer_line(focus_label)
        return {
            "kind": "offer",
            "mode": "gentle",
            "region": region,
            "region_label": focus_label,  # 可能为 None（general）
            "line": line,
            "_next_state": proposing,
        }

    @staticmethod
    def _order_parts(focus: str) -> list[str]:
        order = list(PMR_BODY_PARTS)
        if focus in order:
            order.remove(focus)
            order.insert(0, focus)
        return order

    def _offer_line(self, focus_label: Optional[str]) -> str:
        if focus_label:
            return (f"听起来你现在{focus_label}这边挺紧的……要不要先花一两分钟，"
                    f"我陪你做一组很轻的放松，让它先松一松？不会太久，随时可以停。")
        return ("听起来你现在的身体还绷得很紧……要不要先花一两分钟，"
                "我陪你做一组很轻的放松，让那几处先松下来？不会太久，随时可以停。")

    # ── proposing：等接受/拒绝 ──────────────────────────────────────

    async def _handle_proposing(self, ctx: TurnContext, pmr: dict) -> Optional[dict]:
        text = (ctx.message or "").strip()
        if self._has(text, CRISIS_CUES):
            return None
        # ① 明确拒绝（代码）：拒绝词 / 短拒绝句
        if self._has(text, STOP_CUES) or self._short_refusal(text):
            return self._close_turn(ctx, pmr, reason=_REASON_DECLINED,
                                    extra={"at": "proposing"})
        # ② 明确接受（代码短句）→ 同一轮直接开始 Beat 1（accept 时序 MUST，见下）
        if self._accept_short(text):
            return self._accept_start_turn(ctx, pmr)
        # ③ 模棱两可 → 1 次 response classifier
        out = await self._classify(ctx, pmr, stage="offer")
        kind = out.get("kind", "other")
        if kind in ("affirmative", "neutral"):
            # 🔴 proposing + affirmative/neutral → 本轮返回 start_beat（见 _accept_start_turn）
            return self._accept_start_turn(ctx, pmr)
        if kind == "discomfort":
            return self._close_turn(ctx, pmr, reason=_REASON_DISCOMFORT,
                                    extra={"at": "proposing"})
        # negative / pause / cannot_tense / topic_shift / other / 分类失败 → 不开始
        return self._close_turn(ctx, pmr, reason=_REASON_DECLINED,
                                extra={"at": "proposing"})

    @staticmethod
    def _short_refusal(text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        return len(t) <= 8 and t.startswith(NEG_PREFIXES)

    def _accept_short(self, text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        # 防御性：≤8 字且以 NEG 前缀开头的短句已被 _short_refusal 提前拦截，
        # 此处在完整流程中不会命中；保留是为 _accept_short 单独被调用时也不误判接受。
        if self._has(t, STOP_CUES) or t.startswith(NEG_PREFIXES):
            return False
        if len(t) <= 8 and self._has(t, ACCEPT_CUES):
            return True
        return False

    def _accept_start_turn(self, ctx: TurnContext, pmr: dict) -> dict:
        """🔴 accept 时序：接受后**同一轮**返回 start_beat（说第一区域 Beat 1），
        中间不隔轮；run() 落盘 active/tension/beats_done=1。"""
        order = list(pmr.get("body_parts") or PMR_BODY_PARTS)
        idx = int(pmr.get("part_index") or 0)
        region = pmr.get("current_part") or order[0]
        mode = pmr.get("mode") or "gentle"
        next_state = self._derive(pmr, status="active", mode=mode, part_index=idx,
                                  current_part=region, phase="tension", beats_done=1,
                                  parts_completed=0, closing=False)
        return {
            "kind": "start_beat",
            "mode": mode,
            "region": region,
            "region_label": PMR_BODY_PARTS.get(region, region),
            "beat_no": 1,
            "line": self._tension_line(region, mode),
            "_next_state": next_state,
        }

    # ── active：推进 / 软确认 / 退出（未到收尾轮） ───────────────────

    async def _handle_active(self, ctx: TurnContext, pmr: dict) -> Optional[dict]:
        text = (ctx.message or "").strip()
        if self._has(text, CRISIS_CUES):
            return None  # 交给安全线，不推进不清
        lead = _lead_sentence(text)
        # ① 完全离开放松 → topic_shift 关闭（决策 6：明确不再回应当前练习才算）
        if self._has(text, NEW_TOPIC_LEAVE_CUES):
            return self._close_turn(ctx, pmr, reason=_REASON_TOPIC_SHIFT)
        # ② 身体不适（仅在回应本拍的句子内判断，避免「先回应再顺带提新话题」误关）
        if self._anchored_pain(lead):
            if self._has(text, IMAGINAL_CUES):
                return self._switch_imaginal(ctx, pmr)  # 受伤/不能用力 → 想象式继续
            return self._close_turn(ctx, pmr, reason=_REASON_DISCOMFORT)
        # ③ 停止
        if self._has(text, STOP_CUES):
            return self._close_turn(ctx, pmr, reason=_REASON_STOPPED)
        # ④ 软确认：先回应本拍（短句）→ 继续，不机械等「好了」，也不因顺带话题而停
        if self._soft_continue(lead):
            return self._continue_advance(ctx, pmr)
        # ⑤ 模棱两可 → 1 次 response classifier（分类失败默认继续，不卡死）
        out = await self._classify(ctx, pmr, stage="active")
        kind = out.get("kind", "other")
        if kind in ("affirmative", "neutral", "other"):
            return self._continue_advance(ctx, pmr)
        if kind == "cannot_tense":
            return self._switch_imaginal(ctx, pmr)
        if kind == "discomfort":
            return self._close_turn(ctx, pmr, reason=_REASON_DISCOMFORT)
        if kind == "topic_shift":
            return self._close_turn(ctx, pmr, reason=_REASON_TOPIC_SHIFT)
        # negative / pause / 分类失败
        return self._close_turn(ctx, pmr, reason=_REASON_STOPPED)

    def _anchored_pain(self, text: str) -> bool:
        """判身体不适：需不适 cue + 身体/动作锚点同现，并排除「痛快/麻烦」等良性表达。"""
        t = text or ""
        if self._has(t, _PLEASANT_EXCLUSIONS):
            return False
        return self._has(t, DISCOMFORT_CUES) and self._has(t, BODY_REGION_ANCHORS)

    def _soft_continue(self, lead: str) -> bool:
        if not lead or len(lead) > 10:
            return False
        return self._has(lead, SHORT_SOFT_CONTINUE_CUES)

    # ── 推进：2 拍/区域的单调推进 ──────────────────────────────────

    def _continue_advance(self, ctx: TurnContext, pmr: dict) -> dict:
        order = list(pmr.get("body_parts") or PMR_BODY_PARTS)
        idx = int(pmr.get("part_index") or 0)
        region = order[idx]
        phase = pmr.get("phase") or "tension"
        beats_done = int(pmr.get("beats_done") or 0)
        parts_completed = int(pmr.get("parts_completed") or 0)
        mode = pmr.get("mode") or "gentle"
        target = len(order)
        if phase == "tension":
            # Beat1 已发 → 发同区域 Beat2（release + notice）
            next_state = self._derive(pmr, phase="notice", beats_done=beats_done + 1,
                                      parts_completed=parts_completed + 1, closing=False)
            return self._beat_turn(region, beat_no=2, mode=mode, line=self._release_line(region, mode),
                                   next_state=next_state)
        # phase == "notice"：同区域已完成 → 全部完成则收尾，否则进下一区域 Beat1
        if beats_done >= target * 2:
            next_state = self._derive(pmr, phase="closing", closing=True)
            return {
                "kind": "closing",
                "mode": mode,
                "region": region,
                "region_label": PMR_BODY_PARTS.get(region, region),
                "line": self._closing_line(order),
                "_next_state": next_state,
            }
        idx2 = idx + 1
        region2 = order[idx2]
        next_state = self._derive(pmr, part_index=idx2, current_part=region2,
                                  phase="tension", beats_done=beats_done + 1, closing=False)
        return self._beat_turn(region2, beat_no=1, mode=mode, line=self._tension_line(region2, mode),
                               next_state=next_state)

    def _switch_imaginal(self, ctx: TurnContext, pmr: dict) -> dict:
        """切到想象式引导：若仍在收紧相且未切过，则把当前区域 Beat1 用想象方式重发一遍。"""
        mode = pmr.get("mode") or "gentle"
        phase = pmr.get("phase") or "tension"
        if mode == "imaginal" or phase != "tension":
            return self._continue_advance(ctx, pmr)  # 已想象 / 不在收紧相：照常推进
        order = list(pmr.get("body_parts") or PMR_BODY_PARTS)
        idx = int(pmr.get("part_index") or 0)
        region = order[idx]
        next_state = self._derive(pmr, mode="imaginal")
        return self._beat_turn(region, beat_no=1, mode="imaginal",
                               line=self._tension_line(region, "imaginal"),
                               next_state=next_state)

    def _beat_turn(self, region: str, *, beat_no: int, mode: str, line: str, next_state: dict) -> dict:
        return {
            "kind": "beat",
            "mode": mode,
            "region": region,
            "region_label": PMR_BODY_PARTS.get(region, region),
            "beat_no": beat_no,
            "line": line,
            "_next_state": next_state,
        }

    # ── active + closing:true：收尾反馈 → completed ─────────────────

    def _closing_feedback_turn(self, ctx: TurnContext, pmr: dict) -> dict:
        msg = (ctx.message or "").strip()[:200]
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        closed = self._closed_record(pmr, now_seq, reason=_REASON_COMPLETED,
                                     outcome={"user_feedback": msg or None})
        # Host 不展示收尾块，自然回到普通 daily；状态经 run() 落盘 completed。
        return {"kind": "closing_feedback", "line": None, "_next_state": closed}

    # ── 关闭 / 结束（构造新 dict，绝不原地改 ctx.state） ─────────────

    def _close_turn(self, ctx: TurnContext, pmr: dict, *, reason: str, extra: Optional[dict] = None) -> dict:
        now_seq = self.sessions.next_flow_seq(ctx.session_id)
        closed = self._closed_record(pmr, now_seq, reason=reason, extra=extra)
        return {"kind": "ack_close", "line": None, "reason": reason, "_next_state": closed}

    def _closed_record(self, pmr: dict, now_seq: int, *, reason: str,
                       outcome: Optional[dict] = None,
                       extra: Optional[dict] = None) -> dict:
        completed = int(pmr.get("parts_completed") or 0)
        mode = pmr.get("mode") or "gentle"
        o = {"completed_parts": completed, "mode": mode, **(outcome or {})}
        if extra:
            o.update(extra)
        return {
            "status": "closed",
            "mode": mode,
            "body_parts": list(pmr.get("body_parts") or PMR_BODY_PARTS),
            "part_index": int(pmr.get("part_index") or 0),
            "current_part": pmr.get("current_part"),
            "phase": pmr.get("phase"),
            "beats_done": int(pmr.get("beats_done") or 0),
            "parts_completed": completed,
            "start_seq": pmr.get("start_seq"),
            "closed_seq": now_seq,
            "close_reason": reason,
            "outcome": o,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # response classifier（回应模棱两可时的 1 次轻量 LLM；失败由调用方按 stage 降级）
    # ═══════════════════════════════════════════════════════════════════════════

    async def _classify(self, ctx: TurnContext, pmr: dict, *, stage: str) -> dict:
        skill = self._response_skill()
        if skill is None:
            return {"kind": "other", "reason": "no_skill"}
        bound = self._llm.bind(
            agent="pmr_response",
            user_id=ctx.user_id, session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        try:
            result = await skill.aexecute(
                {"user_text": ctx.message, "situation": self._situation(pmr, stage)},
                {"llm": bound},
            )
        except Exception as e:  # noqa: BLE001 — 分类失败由调用方降级
            app_log("warning", "pmr", "response_failed",
                    trace_id=ctx.trace_id, error=str(e))
            return {"kind": "other", "reason": "response_llm_failed"}
        if not result.success:
            return {"kind": "other", "reason": "response_failed"}
        return result.output

    @staticmethod
    def _situation(pmr: dict, stage: str) -> str:
        if stage == "offer":
            return "系统上一轮向用户轻轻邀请了一次短放松练习（还没有开始），在等 ta 决定要不要做。"
        part = PMR_BODY_PARTS.get(pmr.get("current_part"), pmr.get("current_part") or "？")
        beat_no = int(pmr.get("beats_done") or 0) % 2 + 1  # 最近发到第几拍（1/2）
        return (f"系统正在带用户做渐进式肌肉放松，已经到「{part}」附近。"
                f"上一轮引导了 ta（第 {beat_no} 拍），这轮在等 ta 反馈以决定继续/停/改想象/换话题。")

    # ═══════════════════════════════════════════════════════════════════════════
    # 决策末尾落盘（DecisionLine 独占写 state 的通道之一；无 LLM）
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(self, ctx: TurnContext, *, switched_to_therapy: bool) -> dict:
        """返回要并入 state 的 pmr 更新；无变化返回 {}。step() 已算好 _next_state。"""
        if switched_to_therapy:
            if ctx.state.pmr:
                return {"pmr": None}  # 让位当前疗法流程（signals 亦会清）
            return {}
        turn = ctx.pmr_turn
        if turn is not None and isinstance(turn.get("_next_state"), dict):
            return {"pmr": turn["_next_state"]}
        return {}

    # ── 内部工具 ─────────────────────────────────────────────────────

    @staticmethod
    def _derive(pmr: dict, **overrides) -> dict:
        """基于上轮记录构造新状态 dict（副本，绝不原地改 ctx.state 快照）。"""
        base = {k: pmr.get(k) for k in _STATE_KEYS}
        base.update(overrides)
        return base

    # ── 引导话术（对话层措辞；Host 只做表述层软化，不增加部位/不提前给下一拍） ──
    # 收放按产品节奏 2 拍/区域：Beat1 说收紧（允许随时自行松开，避免“持续紧绷等回复”），
    # Beat2 说放松与留意。imaginal 变体用于受伤/不能用力时的想象式引导。

    def _tension_line(self, region: str, mode: str) -> str:
        gentle = {
            "shoulders": ("我们先从肩膀开始。请自然地坐好，把两边肩膀轻轻往耳朵的方向耸起来，"
                          "感觉肩颈的肌肉微微收紧——不用太用力，保持几秒就好。"
                          "如果觉得不舒服，随时可以自己松开，不用等我。"),
            "hands": ("接下来是双手。请轻轻握拳，把手指和前臂收紧一些——不用握得很紧，"
                      "感受手部肌肉绷起来的感觉，保持几秒。不舒服就随时松开。"),
            "face": ("最后是脸。请轻轻把眉头皱起来，让眼睛周围和脸颊的肌肉也微微收紧——"
                     "不用夸张，只是感觉面部绷起来，保持几秒。不舒服就随时松开。"),
        }
        imaginal = {
            "shoulders": ("这次我们不用真的用力。只请你在心里想象：两只肩膀正被轻轻往上提、"
                          "微微绷紧——光是想象那种感觉就好。保持几秒，然后随时可以松开。"),
            "hands": ("这次不用真的握拳。只在心里想象：双手正轻轻握紧、手臂也微微绷起来——"
                      "想象那种紧就好，保持几秒，随时可以松开。"),
            "face": ("这次不用真的皱眉头。只在心里想象：整张脸正轻轻绷紧、眉头微微聚拢——"
                     "想象那种紧就好，保持几秒，随时可以松开。"),
        }
        return (imaginal if mode == "imaginal" else gentle).get(region, "")

    def _release_line(self, region: str, mode: str) -> str:
        gentle = {
            "shoulders": ("现在慢慢把肩膀放下来，让它自然地往下沉。"
                          "留意刚才发紧的地方一点点松开、变软的感觉，停在这儿感受一小会儿，"
                          "看看和刚才有什么不同。"),
            "hands": ("现在慢慢松开手指，让手掌摊开、自然放松。"
                      "留意手一点点变轻、变热、松开的感觉，停在这儿感受一小会儿。"),
            "face": ("现在慢慢松开，让眉毛落回原位、眼皮放松、嘴角也松下来。"
                     "留意整张脸从绷着到松开的感觉，停一小会儿感受它。"),
        }
        imaginal = {
            "shoulders": ("现在在心里想象那股绷紧像解开的绳结一样慢慢松开，肩膀往下沉、变轻。"
                          "留意这种慢慢松开的感觉，停一小会儿感受它。"),
            "hands": ("现在想象握紧的双手慢慢松开、手指一根根舒展开，手变得轻松又暖和。"
                      "留意这种松开的感觉，停一小会儿。"),
            "face": ("现在想象脸上的绷紧像被温水慢慢化开，眉毛、眼皮、嘴角都松下来。"
                     "留意脸一点点放松变软的感觉，停一小会儿。"),
        }
        return (imaginal if mode == "imaginal" else gentle).get(region, "")

    @staticmethod
    def _closing_line(order: list[str]) -> str:
        labels = "、".join(PMR_BODY_PARTS.get(r, r) for r in order)
        return (f"今天先陪你到这里——把{labels}都慢慢松了一遍。现在感觉怎么样？"
                f"身体有没有比刚才松一些？")
