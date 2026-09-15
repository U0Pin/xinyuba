"""决策线：疗法决策（滑动窗口）（Phase 3）。

- TherapyDecider：日常对话状态下，窗口/token 阈值触发；输入最近 WINDOW_SIZE 轮
  窗口 + 用户画像 + session 上下文；输出 需要/不需要 + 哪个疗法。

疗程内的持续/停止判断自 2026-09 起由定量边际效用公式裁决
（core/marginal_utility.py），原 LLM"继续/结束评估"（ContinueEval）已退役——
它没有"效用"的可测量输入，只是对同一份对话的又一次主观投票。

**错误可见化（2026-09 fix）**：之前 LLM 调用失败/超时/限流会被
`BoundLLMClient.ajson` 静默吞掉返回 `{}` → `need_therapy=False` → 看起来
"为什么不触发疗法？"的假象。本模块现在显式：
  1. 给 `ajson` 传 `failure_log=("decision", "therapy_decide_failed")`，
     任何 provider/网络/解析异常都会在 app.jsonl 留一行 warning；
  2. 模型答复 `need_therapy=true` 但 `therapy` 不在 THERAPIES 枚举里
     （包括 missing/打字错误/幻觉）时，再补一条
     `therapy_decide_invalid` warning。
"""

import json

from src.agents.prompts.therapy import (
    THERAPY_DECISION_OUTPUT_SCHEMA,
    THERAPY_DECISION_PROMPT,
)
from src.core.llm_client import LLMClient
from src.core.logging_utils import app_log
from src.core.state import THERAPIES
from src.utils.text import format_transcript


class TherapyDecider:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def build_prompt(self, *, window: list[dict], profile: dict,
                     summary: dict, user_text: str) -> str:
        window_str = format_transcript(window)
        profile_str = json.dumps(profile, ensure_ascii=False) if profile else "（暂无）"
        summary_str = json.dumps(summary.get("summary", []), ensure_ascii=False) if summary else "（暂无）"
        return f"""{THERAPY_DECISION_PROMPT}

## 当前对话

最近窗口对话：
{window_str if window_str else "（对话刚开始）"}

当前用户消息：{user_text}
用户画像：{profile_str}
会话上下文摘要：{summary_str}

## Output JSON
{THERAPY_DECISION_OUTPUT_SCHEMA}"""

    async def decide(self, ctx, window: list[dict]) -> dict:
        prompt = self.build_prompt(
            window=window, profile=ctx.profile,
            summary=ctx.summary, user_text=ctx.message,
        )
        bound = self._llm.bind(
            agent="therapy_decider", user_id=ctx.user_id,
            session_id=ctx.session_id, trace_id=ctx.trace_id,
        )
        data = await bound.ajson(
            prompt,
            failure_log=("decision", "therapy_decide_failed"),
        )
        need = bool(data.get("need_therapy", False))
        therapy = data.get("therapy")
        if therapy not in THERAPIES:
            if need:
                # 模型说"需要疗法"但给了非枚举/缺失的疗法名——明显异常，
                # 写一行 warning 让研究者/调试者能立刻看出"不是静默吃掉"。
                app_log("warning", "decision", "therapy_decide_invalid",
                        trace_id=ctx.trace_id, raw_therapy=therapy, need_therapy=need)
            therapy = None
            need = False
        return {"need_therapy": need, "therapy": therapy, "reason": data.get("reason", "")}
