"""沉淀线（Phase 2）：用户画像增量更新 + session 上下文维护。

- 每轮对话结束后由调度器异步调用（不阻塞 SSE 响应，只占 session 锁）；
- 画像：LLM 增量更新（读现有画像 + 最新一轮对话 → 变更 patch），user 级共享；
- 摘要：流水 token 累计达到阈值时重生成（输入 = 已有摘要 + 新增流水）；
- 单写者：本线独占画像与摘要的写入；画像写入持 user 级锁（跨 session 共享）。
"""

import asyncio
import json

from datetime import datetime, timezone

from src.agents.prompts.portrait import (
    ACT_DIMENSIONS,
    PERSONALITY_TYPES,
    PORTRAIT_EVAL_PROMPT,
    PORTRAIT_OUTPUT_SCHEMA,
)
from src.agents.prompts.settlement import (
    PROFILE_OUTPUT_SCHEMA,
    PROFILE_UPDATE_PROMPT,
    SUMMARY_OUTPUT_SCHEMA,
    SUMMARY_PROMPT,
)
from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.store.portrait_store import PortraitStore
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore
from src.utils.config import config
from src.utils.text import est_tokens_half


def build_profile_update_prompt(profile: dict, user_text: str, agent_text: str) -> str:
    """画像增量 prompt（被黄金快照冻结）。"""
    profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
    return f"""{PROFILE_UPDATE_PROMPT}

## 现有画像
{profile_str}

## 最新一轮对话
User: {user_text}
Agent: {agent_text}

Output JSON:
{PROFILE_OUTPUT_SCHEMA}"""


def build_portrait_eval_prompt(profile: dict, user_text: str = "", agent_text: str = "") -> str:
    """ACT 六维 + 人格 + 价值词评估 prompt（画像 + 最近一轮对话，user 级）。"""
    profile_str = json.dumps(profile, ensure_ascii=False) if profile else "{}"
    return f"""{PORTRAIT_EVAL_PROMPT}

## 用户画像
{profile_str}

## 最近一轮对话
User: {user_text}
Agent: {agent_text}

Output JSON:
{PORTRAIT_OUTPUT_SCHEMA}"""


def parse_value_words(data: dict) -> list[str]:
    """校验价值词：只保留字符串项，上限 5 个。"""
    raw = data.get("value_words")
    if not isinstance(raw, list):
        return []
    return [w for w in raw if isinstance(w, str) and w.strip()][:5]


def parse_act_metrics(data: dict) -> dict:
    """校验六维：只保留合法维度名与 [0,1] 内的数值，非法项丢弃。"""
    raw = data.get("act_metrics")
    if not isinstance(raw, dict):
        return {}
    metrics = {}
    for k in ACT_DIMENSIONS:
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0:
            metrics[k] = float(v)
    return metrics


def parse_personality(data: dict) -> str | None:
    """校验人格枚举；非法值返回 None（保留旧值）。"""
    p = data.get("personality")
    return p if p in PERSONALITY_TYPES else None


def build_summary_prompt(summary: dict, new_entries: list[dict]) -> str:
    """摘要重生成 prompt（被黄金快照冻结）。"""
    lines = []
    for e in new_entries:
        role = "用户" if e.get("role") == "user" else "AI"
        lines.append(f"[{e.get('seq')}] {role}：{e.get('text', '')}")
    flow_str = "\n".join(lines) if lines else "（无新增）"
    summary_str = json.dumps(summary, ensure_ascii=False) if summary else "[]"
    return f"""{SUMMARY_PROMPT}

## 已有摘要
{summary_str}

## 新增对话流水
{flow_str}

Output JSON:
{SUMMARY_OUTPUT_SCHEMA}"""


class SettlementLine:
    def __init__(
        self,
        llm: LLMClient,
        session_store: SessionStore | None = None,
        profile_store: ProfileStore | None = None,
        portrait_store: PortraitStore | None = None,
    ):
        self._llm = llm
        self.sessions = session_store or SessionStore()
        self.profiles = profile_store or ProfileStore()
        self.portraits = portrait_store or PortraitStore()
        self._user_locks: dict[str, asyncio.Lock] = {}
        # 正在评估画像页数据的 user 集合（HTTP 层据此区分 pending / none）
        self.portrait_pending: set[str] = set()

    def _user_lock(self, user_id: str) -> asyncio.Lock:
        return self._user_locks.setdefault(user_id, asyncio.Lock())

    async def run_after_turn(self, *, user_id: str, session_id: str, trace_id: str) -> None:
        """一轮对话结束后异步执行：画像增量 + 摘要阈值维护。"""
        async with self._user_lock(user_id):
            await self._update_profile(user_id, session_id, trace_id)
            await self._update_portrait(user_id, session_id, trace_id)
            await self._maintain_summary(session_id, trace_id)

    # ── 画像增量 ───────────────────────────────────────────────────

    def _latest_turn_texts(self, session_id: str) -> tuple[str, str]:
        tail = self.sessions.read_flow_tail(session_id, 2)
        user_text, agent_text = "", ""
        for e in tail:
            if e.get("role") == "user":
                user_text = e.get("text", "")
            else:
                agent_text = e.get("text", "")
        return user_text, agent_text

    async def _update_profile(self, user_id: str, session_id: str, trace_id: str) -> None:
        user_text, agent_text = self._latest_turn_texts(session_id)

        profile = self.profiles.get(user_id)
        prompt = build_profile_update_prompt(profile, user_text, agent_text)
        bound = self._llm.bind(
            agent="settlement_profile",
            user_id=user_id,
            session_id=session_id,
            trace_id=trace_id,
        )
        data = await bound.ajson(prompt, failure_log=("settlement", "profile_update_failed"))
        self._merge_profile_patch(user_id, profile, data)

    def _merge_profile_patch(self, user_id: str, profile: dict, data: dict) -> None:
        if not data or data.get("no_change"):
            return
        updates = data.get("updates") or {}
        if isinstance(updates, dict):
            for k, v in updates.items():
                profile[k] = v
        appends = data.get("append_lists") or {}
        if isinstance(appends, dict):
            for k, items in appends.items():
                if not isinstance(items, list):
                    continue
                lst = profile.setdefault(k, [])
                if not isinstance(lst, list):
                    lst = []
                for it in items:
                    if it not in lst:
                        lst.append(it)
                profile[k] = lst
        self.profiles.save(user_id, profile)

    # ── 画像页评估（ACT 六维 + 人格）─────────────────────────────────

    async def _update_portrait(self, user_id: str, session_id: str, trace_id: str) -> None:
        """每轮顺带重估 ACT 六维 + 人格 + 价值词。

        无需对话积累：画像为空时也基于最近一轮对话给保守估计（prompt 已要求）。
        价值词空列表也落盘：「算完但没提取到」是 ready + []（空态），不是 pending。
        """
        self.portrait_pending.add(user_id)
        try:
            user_text, agent_text = self._latest_turn_texts(session_id)
            profile = self.profiles.get(user_id)
            bound = self._llm.bind(
                agent="portrait_eval", user_id=user_id, session_id=session_id, trace_id=trace_id,
            )
            data = await bound.ajson(
                build_portrait_eval_prompt(profile, user_text, agent_text),
                failure_log=("settlement", "portrait_eval_failed"),
            )
        finally:
            self.portrait_pending.discard(user_id)
        if not data:
            return
        portrait = self.portraits.get(user_id)
        metrics = parse_act_metrics(data)
        personality = parse_personality(data)
        if metrics:
            portrait["act_metrics"] = metrics
        if personality:
            portrait["personality"] = personality
        portrait["value_words"] = parse_value_words(data)
        portrait["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.portraits.save(user_id, portrait)

    async def regenerate_personality(self, user_id: str, session_id: str = "") -> bool:
        """「重新生成报告」：只重判人格类型（不动六维、价值词等其他字段）。

        无需画像积累：画像为空时也基于最近对话给保守判定；
        LLM 输出非法时返回 False（保留旧值）。频控由 HTTP 层负责。
        """
        async with self._user_lock(user_id):
            profile = self.profiles.get(user_id)
            user_text, agent_text = (
                self._latest_turn_texts(session_id) if session_id else ("", "")
            )
            bound = self._llm.bind(agent="portrait_personality", user_id=user_id)
            data = await bound.ajson(
                build_portrait_eval_prompt(profile, user_text, agent_text),
                failure_log=("settlement", "personality_regen_failed"),
            )
            personality = parse_personality(data)
            if not personality:
                return False
            portrait = self.portraits.get(user_id)
            portrait["personality"] = personality
            portrait["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.portraits.save(user_id, portrait)
            return True

    # ── 摘要阈值维护 ───────────────────────────────────────────────

    async def _maintain_summary(self, session_id: str, trace_id: str) -> None:
        summ = self.sessions.read_summary(session_id)
        updated_seq = int(summ.get("updated_seq", 0))
        new_entries = self.sessions.read_flow_range(session_id, start_seq=updated_seq + 1)
        if not new_entries:
            return
        added = sum(est_tokens_half(e.get("text", "")) for e in new_entries)
        token_estimate = int(summ.get("token_estimate", 0)) + added
        new_updated_seq = new_entries[-1]["seq"]
        summary = summ.get("summary", [])

        if token_estimate >= config.SUMMARY_TOKEN_THRESHOLD:
            prompt = build_summary_prompt(summary, new_entries)
            bound = self._llm.bind(agent="settlement_summary", session_id=session_id, trace_id=trace_id)
            try:
                result = await bound.acomplete(prompt, json_mode=True)
                data = result.data or {}
                new_summary = data.get("summary")
                if isinstance(new_summary, list):
                    summary = new_summary
                else:
                    app_log("warning", "settlement", "summary_invalid_shape", trace_id=trace_id)
            except Exception as e:
                app_log("warning", "settlement", "summary_update_failed",
                        trace_id=trace_id, error=str(e))
            token_estimate = 0

        self.sessions.write_summary(session_id, {
            "updated_seq": new_updated_seq,
            "token_estimate": token_estimate,
            "summary": summary,
        })
