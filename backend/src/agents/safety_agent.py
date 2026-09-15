"""安全决策 Agent（决策线）。

语义（v2 沉淀稿第七节）：
- 每条用户消息都触发；廉价模型；5 级风险阶梯沿用；
- 所有级别都标记记录（日志 + 调度状态）；仅 CRISIS 触发危机应对；
- 任何失败回退 SAFE（宁可漏报，不阻塞会话）。
"""

from src.agents.prompts.safety import SAFETY_OUTPUT_SCHEMA, SAFETY_PROMPT
from src.core.llm_client import LLMClient
from src.core.state import RiskState
from src.utils.config import config


class SafetyAgent:
    def __init__(self, llm: LLMClient):
        self._llm = llm

    def build_prompt(self, *, user_text: str, current_risk: str) -> str:
        """拼装安全决策 prompt（被黄金快照冻结）。"""
        return f"""{SAFETY_PROMPT}

Current risk state: {current_risk}

User message:
{user_text}

Output JSON:
{SAFETY_OUTPUT_SCHEMA}"""

    async def assess(
        self,
        *,
        user_id: str,
        session_id: str,
        trace_id: str,
        user_text: str,
        current_risk: str,
    ) -> dict:
        """返回 {"risk_level": RiskState, "risk_type": str}；失败回退 SAFE。"""
        prompt = self.build_prompt(user_text=user_text, current_risk=current_risk)
        bound = self._llm.bind(
            agent="safety", user_id=user_id, session_id=session_id, trace_id=trace_id
        )
        data = await bound.ajson(prompt, model=config.CHEAP_MODEL_NAME)
        try:
            level = RiskState(data.get("risk_level", "SAFE"))
        except ValueError:
            level = RiskState.SAFE
        return {"risk_level": level, "risk_type": data.get("risk_type", "none")}
