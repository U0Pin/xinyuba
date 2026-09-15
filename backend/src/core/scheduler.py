"""纯代码调度器（新架构核心，Phase 2/3）。

三条线并行、说话不阻塞（v2 沉淀稿第四节第 6 条时序总方案）：

    读存盘状态 → 并行启动 对话线(流式) 与 决策线(安全+疗法) → await 对话（SSE 结束）
    → await 决策 → 轮边界应用信号写 state.json（下一轮生效）→ await 沉淀 → 释放 session 锁

- 同一 session 同时只允许一轮在处理（session 锁），从机制上消除竞态；
- 单写者：流水 → 对话线；state.json → 本调度器（core/state.py 的 SessionState
  是其唯一 schema）；画像/摘要 → 沉淀线；
- 当轮回复基于上一轮已完成的决策结果（滞后一轮，作者已接受）；
- 危机从危险消息的下一轮开始生效；危机期间疗法决策与编排暂停；
- Phase 3：疗法决策（滑动窗口/token 阈值）、疗程编排（评估/步骤判断/skill 执行每轮）、
  继续/结束评估（基本流程完成后每轮）、10 轮上限。
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from src.core.logging_utils import app_log, new_trace_id
from src.core.marginal_utility import (
    UtilityRecord,
    compute_record,
    decide,
    state_score,
)
from src.core.signals import (
    CrisisCleared,
    CrisisDetected,
    EndTherapy,
    Signal,
    SwitchToTherapy,
)
from src.agents.emotion_support import EmotionSupportOrchestrator
from src.core.state import (
    Orchestration,
    RiskState,
    SessionState,
)
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.store.user_info_store import UserInfoStore
from src.utils.config import config
from src.utils.text import est_tokens_half


@dataclass
class TurnContext:
    """一轮请求内的共享上下文（各线从存盘读取，经由此对象传递）。

    本对象是一轮的**只读快照**：决策线要改状态必须经 DecisionResult
    回传、轮边界生效；对话线只读。
    """

    user_id: str
    session_id: str
    trace_id: str
    message: str
    state: SessionState
    profile: dict
    summary: dict
    orchestration: Orchestration
    affect_labeling_plan: Optional[dict] = None  # 本轮 Affect Labeling 提议（对话线只读）
    pmr_turn: Optional[dict] = None              # 本轮 PMR 判定（对话线只读，见 PMR engine）
    grounding_turn: Optional[dict] = None        # 本轮 Grounding 判定（对话线只读，见 grounding engine）


@dataclass
class DecisionResult:
    """决策线一轮的产出：信号 + 风险标记 + 调度状态更新。

    state_updates 的键必须是 SessionState 字段名，值已是对应类型
    （如 therapy 为 TherapyProgress）。
    """

    signals: list = field(default_factory=list)
    risk_level: RiskState = RiskState.SAFE
    risk_type: str = "none"
    state_updates: dict = field(default_factory=dict)


class DialogueLine:
    """对话线：唯一 Host Agent 说话；对话流水的唯一写入者。"""

    def __init__(
        self,
        host_agent,
        session_store: SessionStore | None = None,
    ):
        self.host = host_agent
        self.sessions = session_store or SessionStore()

    async def speak(self, ctx: TurnContext) -> AsyncIterator[dict]:
        """产出 SSE token 事件；内部负责流水写入（user 条目 + agent 条目）。"""
        owner = ctx.state.owner
        recent = self.sessions.read_flow_tail(ctx.session_id, config.RECENT_TURNS)

        self.sessions.append_flow(ctx.session_id, {
            "role": "user",
            "owner": owner,
            "text": ctx.message,
            "trace_id": ctx.trace_id,
        })

        # 路由由 Host 依调度状态裁决（危机 > 疗法 > 日常）；
        # 流水的 owner 标签仍是调度状态里的接管者标识，作审计用（写入值不变）。
        stream = self.host.reply_stream(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            user_text=ctx.message,
            state=ctx.state,
            profile=ctx.profile,
            summary=ctx.summary,
            recent=recent,
            orchestration=ctx.orchestration.to_dict(),
            affect_labeling_plan=ctx.affect_labeling_plan,
            pmr_turn=ctx.pmr_turn,
            grounding_turn=ctx.grounding_turn,
        )

        parts: list[str] = []
        record = None
        try:
            async for token in stream:
                parts.append(token)
                yield {"event": "token", "data": token}
            record = stream.record
        finally:
            # 客户端断开（GeneratorExit）或流异常时也要把已收到的 token 写进流水，
            # 否则用户刷新页面回到 session 后只看到自己的消息，看不到任何回复。
            if parts:
                entry = {
                    "role": "agent",
                    "owner": owner,
                    "text": "".join(parts),
                    "trace_id": ctx.trace_id,
                }
                if record is not None:
                    entry["tokens"] = {
                        "prompt": record.prompt_tokens,
                        "completion": record.completion_tokens,
                    }
                    entry["latency_ms"] = record.latency_ms
                self.sessions.append_flow(ctx.session_id, entry)


class DecisionLine:
    """决策线：安全决策（每条消息）+ 疗法决策（日常态）+ 疗程编排 + 继续/结束评估。"""

    def __init__(
        self,
        safety_agent,
        session_store: SessionStore | None = None,
        therapy_decider=None,
        therapy_agents: dict | None = None,
        affect_engine=None,
        pmr_engine=None,
        grounding_engine=None,
    ):
        self.safety = safety_agent
        self.sessions = session_store or SessionStore()
        self.therapy_decider = therapy_decider
        self.therapy_agents = therapy_agents or {}
        # Affect Labeling 决策引擎（可选依赖）：不装配（None）时本线对情绪支持零感知，
        # 所有既有 DecisionLine 构造点行为完全不变。
        self.affect_engine = affect_engine
        # PMR 决策引擎（可选依赖，优先级高于 Affect、与之互斥）：同上，None 则零感知。
        self.pmr_engine = pmr_engine
        # Grounding 决策引擎（可选依赖，优先级介于 PMR 与 Affect 之间）：同上。
        self.grounding_engine = grounding_engine
        # Emotion Support Orchestrator：集中 PMR / Grounding / Affect 的编排（薄、确定性）。
        # 保留 pmr_engine/affect_engine 属性仅为向后兼容，内部统一走 emotion_support。
        self.emotion_support = EmotionSupportOrchestrator(
            pmr_engine=pmr_engine,
            affect_engine=affect_engine,
            grounding_engine=grounding_engine,
            session_store=session_store,
        )

    async def run_turn(self, ctx: TurnContext) -> DecisionResult:
        """一轮决策：安全 →（疗程编排 | 日常疗法决策）→ 情绪支持。

        步骤顺序即语义（危机短路、情绪支持给疗法与安全让位），
        本函数保持"读一遍就是一条时间线"；各步骤细节见对应私有方法。
        """
        signals: list[Signal] = []
        updates: dict = {}

        # 1. 安全决策（每条消息，廉价模型）；危机进入/解除以信号表达
        risk, risk_type = await self._assess_safety(ctx, signals)

        # 2. 危机期间疗法决策与编排暂停（直到危险解除）
        if risk == RiskState.CRISIS:
            return DecisionResult(signals=signals, risk_level=risk, risk_type=risk_type)

        # 3. 疗程中 → 编排；日常态 → 疗法决策
        if ctx.state.therapy is not None:
            sigs, ups = await self._orchestrate(ctx)
        else:
            sigs, ups = await self._maybe_decide_therapy(ctx)
        signals += sigs
        updates.update(ups)

        # 4. 情绪支持（PMR 优先、其次 Affect；互斥、均可选装配）
        updates.update(await self._emotion_updates(ctx, signals, risk))

        return DecisionResult(signals=signals, risk_level=risk,
                              risk_type=risk_type, state_updates=updates)

    async def _assess_safety(self, ctx: TurnContext, signals: list) -> tuple:
        """安全决策并转入危机信号；返回 (风险档位, 风险类型)。"""
        result = await self.safety.assess(
            user_id=ctx.user_id,
            session_id=ctx.session_id,
            trace_id=ctx.trace_id,
            user_text=ctx.message,
            current_risk=ctx.state.risk_level,
        )
        app_log("info", "decision", "safety_done", trace_id=ctx.trace_id, **result)
        risk = result["risk_level"]
        if risk == RiskState.CRISIS and not ctx.state.crisis:
            signals.append(CrisisDetected())
        elif risk != RiskState.CRISIS and ctx.state.crisis:
            signals.append(CrisisCleared())
        return risk, result["risk_type"]

    async def _emotion_updates(self, ctx: TurnContext, signals: list, risk: RiskState) -> dict:
        """情绪支持决策：统一委托 ESO（PMR 优先、其次 Affect；互斥、均可选装配）。"""
        switched = any(isinstance(s, SwitchToTherapy) for s in signals)
        return await self.emotion_support.run(ctx, switched_to_therapy=switched, risk=risk)

    # ── 疗法决策（日常状态，滑动窗口 + token 阈值） ───────────────

    async def _maybe_decide_therapy(self, ctx: TurnContext):
        if self.therapy_decider is None:
            return [], {}
        threshold = config.WINDOW_TOKEN_THRESHOLD
        last_check = ctx.state.last_therapy_check_seq
        new_entries = self.sessions.read_flow_range(ctx.session_id, start_seq=last_check + 1)
        user_entries = [e for e in new_entries if e.get("role") == "user"]
        last_seq = user_entries[-1]["seq"] if user_entries else last_check
        pending = ctx.state.pending_user_tokens + sum(
            est_tokens_half(e.get("text", "")) for e in user_entries
        )
        updates = {"last_therapy_check_seq": last_seq, "pending_user_tokens": pending}
        if threshold > 0 and pending < threshold:
            return [], updates  # 未达阈值：只累计，不评估

        window = self.sessions.read_flow_tail(ctx.session_id, config.WINDOW_SIZE)
        decision = await self.therapy_decider.decide(ctx, window)
        app_log("info", "decision", "therapy_decide_done", trace_id=ctx.trace_id, **decision)
        updates["pending_user_tokens"] = 0
        signals = []
        if decision.get("need_therapy") and decision.get("therapy"):
            signals.append(SwitchToTherapy(
                therapy=decision["therapy"],
                start_seq=self.sessions.next_flow_seq(ctx.session_id),
            ))
        return signals, updates

    # ── 疗程编排（每轮：评估 → 步骤判断 → skill 执行 → 推进） ─────

    async def _orchestrate(self, ctx: TurnContext):
        signals: list[Signal] = []
        name = ctx.state.therapy.name
        agent = self.therapy_agents.get(name)
        if agent is None:
            app_log("warning", "decision", "unknown_therapy", trace_id=ctx.trace_id, therapy=name)
            return [EndTherapy(reason="unknown_therapy")], {}

        # 快照拷贝：本轮全部推进只改副本，轮边界经 state_updates 回传（单写者纪律）。
        therapy = ctx.state.therapy.copy()
        prev = ctx.orchestration
        products = dict(prev.products or {})
        rounds = therapy.rounds

        # 1) 状态评估（每轮）
        assessment = await agent.run_assessment(ctx)
        # 首次编排：按评估路由确定初始步骤
        if therapy.step_index == -1:
            therapy.step_index = agent.initial_step_index(assessment)
            therapy.branch = None
        step_before = agent.current_step(therapy)

        # 2) 步骤推进判断（每轮）
        judgment = await agent.run_step_judgment(ctx, assessment, products, therapy)
        # 3) 执行当前步骤 skill（每轮）
        skill_output = await agent.run_current_skill(ctx, assessment, products, therapy)
        products = agent.products_update(
            step_name=step_before,
            skill_name=(skill_output or {}).get("_skill_name", ""),
            skill_output=skill_output or {},
            products=products,
        )
        # 4) 推进
        agent.apply_judgment(therapy, judgment, assessment)
        therapy.rounds = rounds + 1

        # 5) 边际效用账（B1：全周期裁决，替代原"完成后才问 LLM"的继续评估）
        prev_rec = (UtilityRecord(**therapy.utility_history[-1])
                    if therapy.utility_history else None)
        score = state_score(name, assessment, skill_output)
        if score is None and prev_rec is not None:
            score = prev_rec.state_score  # CBT 等稀疏信号：携带上一轮值（Δ=0）
        record = compute_record(
            round_no=therapy.rounds,
            judgment_action=judgment.get("action", "stay"),
            skill_output=skill_output, score=score,
            prev=prev_rec, history=therapy.utility_history,
        )
        therapy.utility_history.append(record.to_dict())
        verdict = decide(therapy.utility_history)
        app_log("info", "decision", "utility_update", trace_id=ctx.trace_id,
                therapy=name, **record.to_dict(), stop=verdict.stop, rule=verdict.rule)

        orchestration = Orchestration(
            updated_seq=prev.updated_seq + 1,
            therapy=name,
            current_step=step_before,  # 本轮编排所对应的步骤（对话线下一轮消费）
            next_step=agent.current_step(therapy),
            step_judgment=judgment,
            assessment=assessment,
            skill_output=skill_output,
            products=products,
            utility_eval={**record.to_dict(), "stop": verdict.stop, "rule_hit": verdict.rule},
        )

        # 6) 停止裁决：硬上限兜底优先，其次效用公式的停滞/倒退判定
        if therapy.rounds >= config.THERAPY_MAX_ROUNDS:
            signals.append(EndTherapy(reason="round_cap"))
        elif verdict.stop:
            signals.append(EndTherapy(reason=verdict.rule or "utility"))

        self.sessions.write_orchestration(ctx.session_id, orchestration.to_dict())
        app_log("info", "decision", "orchestration_done", trace_id=ctx.trace_id,
                therapy=name, step=step_before, judgment=judgment.get("action"),
                skill=(skill_output or {}).get("_skill_name"))
        return signals, {"therapy": therapy}


class Scheduler:
    """主调度器：三线编排、信号裁决、session 串行化、单写者纪律。"""

    def __init__(
        self,
        *,
        dialogue_line: DialogueLine,
        decision_line: DecisionLine,
        settlement_line,
        session_store: SessionStore | None = None,
        profile_store: ProfileStore | None = None,
        user_info_store: UserInfoStore | None = None,
    ):
        self.dialogue_line = dialogue_line
        self.decision_line = decision_line
        self.settlement_line = settlement_line
        self.sessions = session_store or SessionStore()
        self.profiles = profile_store or ProfileStore()
        self.user_infos = user_info_store or UserInfoStore()
        self.portraits = self.settlement_line.portraits
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def handle_message(self, user_id: str, session_id: str, message: str) -> AsyncIterator[dict]:
        """处理一条用户消息，产出 SSE 事件（token…、final；异常时 error）。"""
        trace_id = new_trace_id()
        app_log(
            "info", "scheduler", "turn_start",
            trace_id=trace_id, user_id=user_id, session_id=session_id,
            user_text=message,
        )
        async with self._session_lock(session_id):
            state = SessionState.from_dict(self.sessions.read_state(session_id))
            profile = self.profiles.get(user_id)
            user_info = self.user_infos.get(user_id)
            if user_info:
                # 设置页四项用户信息并入画像，随系统提示词对 Agent 可见
                # （提示词模板不变，仅内容多一个 user_info 键）。
                profile = {**profile, "user_info": user_info}
            summary = self.sessions.read_summary(session_id)
            orchestration = Orchestration.from_dict(self.sessions.read_orchestration(session_id))
            ctx = TurnContext(
                user_id=user_id,
                session_id=session_id,
                trace_id=trace_id,
                message=message,
                state=state,
                profile=profile,
                summary=summary,
                orchestration=orchestration,
            )

            # 情绪支持编排（同轮提问时序）：由 ESO 统一决定本轮带出哪个 skill 的引导块，
            # 并把产出写回 ctx.pmr_turn / ctx.affect_labeling_plan（PMR 优先、互斥）。
            await self.decision_line.emotion_support.plan(ctx)

            # T2 决策线：与说话并行
            decision_task = asyncio.create_task(self.decision_line.run_turn(ctx))

            # T1 对话线：说话（SSE 转发）
            try:
                async for event in self.dialogue_line.speak(ctx):
                    yield event
            except Exception as e:
                app_log("error", "scheduler", "dialogue_failed",
                        trace_id=trace_id, error=str(e))
                yield {"event": "error", "data": {"error": "dialogue failed"}}

            # 对话线结束：通知前端"主回复已完，光标可停"（不依赖关流，
            # 即便后续决策/沉淀还在跑，前端也能立刻消光标）
            yield {"event": "dialogue_done", "data": {
                "trace_id": trace_id,
                "owner": state.owner,
            }}

            # 决策线结果（信号必须在本轮结束前落到状态，下一轮生效）
            try:
                decision = await decision_task
            except Exception as e:
                app_log("error", "scheduler", "decision_failed",
                        trace_id=trace_id, error=str(e))
                decision = DecisionResult()

            new_state = self._apply_signals(ctx, decision)
            self.sessions.write_state(session_id, new_state.to_dict())

            # T3 沉淀线（画像 + 摘要；响应已结束，不阻塞首字）
            try:
                await self.settlement_line.run_after_turn(
                    user_id=user_id, session_id=session_id, trace_id=trace_id
                )
            except Exception as e:
                app_log("error", "scheduler", "settlement_failed",
                        trace_id=trace_id, error=str(e))

            final_data = {
                "message": message,
                "risk_state": new_state.risk_level.value,
                "crisis": new_state.crisis,
            }
            if os.getenv("DEBUG_TALKS") == "1":
                latest = Orchestration.from_dict(self.sessions.read_orchestration(session_id))
                final_data.update({
                    "planned_skill": (latest.skill_output or {}).get("_skill_name"),
                    "current_therapy": new_state.therapy.name if new_state.therapy else None,
                    "intervention_count": new_state.therapy.rounds if new_state.therapy else 0,
                })
            yield {"event": "final", "data": final_data}
            app_log("info", "scheduler", "turn_end", trace_id=trace_id,
                    user_id=user_id, session_id=session_id,
                    risk_level=new_state.risk_level.value, crisis=new_state.crisis)

    # ── 轮边界信号应用（调度状态唯一写入点） ───────────────────────

    def _apply_signals(self, ctx: TurnContext, decision: DecisionResult) -> SessionState:
        # 从快照拷贝起步（ctx.state 是与对话线共享的只读快照，不可原地改）：
        # 决策线的更新先合并、信号后应用——信号（如 EndTherapy）必须能
        # 覆盖编排的状态更新；最后记录风险档位。
        state = ctx.state.copy()
        for key, value in decision.state_updates.items():
            setattr(state, key, value)
        for sig in decision.signals:
            sig.apply(state)
        state.risk_level = decision.risk_level
        return state
