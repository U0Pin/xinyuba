"""装配入口：把全部部件组装成可运行的 Scheduler（server 与 CLI 共用）。

本模块不依赖 fastapi——CLI 与评测脚本 import 装配时不会被迫拉起 web 栈。
LLMClient 的 provider 为惰性实例化（见 core/llm_client.py），因此 import
本模块及 `build_scheduler()` 的组装本身都不需要凭据；只有真正发起
LLM 调用时才要求 OPENAI_API_KEY 等配置。
"""

from src.agents.affect_labeling import AffectLabelingEngine
from src.agents.grounding import GroundingEngine
from src.agents.host_agent import HostDialogueAgent
from src.agents.progressive_muscle_relaxation import ProgressiveMuscleRelaxationEngine
from src.agents.safety_agent import SafetyAgent
from src.agents.settlement import SettlementLine
from src.agents.therapy import (
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.agents.therapy_decider import TherapyDecider
from src.core.llm_client import LLMClient
from src.core.scheduler import DecisionLine, DialogueLine, Scheduler
from src.store.profile_store import ProfileStore
from src.store.session_store import SessionStore

import src.skills  # noqa: F401 — 注册全部疗法技能（编排依赖 skill_registry）


def build_scheduler(
    llm: LLMClient | None = None,
    session_store: SessionStore | None = None,
    profile_store: ProfileStore | None = None,
) -> Scheduler:
    """组装三线调度器（可注入假件供测试）。"""
    llm = llm or LLMClient()
    sessions = session_store or SessionStore()
    profiles = profile_store or ProfileStore()
    host = HostDialogueAgent(llm, session_store=sessions)
    safety = SafetyAgent(llm)
    therapy_agents = {
        "CBT": CbtTherapyAgent(llm, session_store=sessions),
        "ACT": ActTherapyAgent(llm, session_store=sessions),
        "DBT": DbtTherapyAgent(llm, session_store=sessions),
        "MI": MiTherapyAgent(llm, session_store=sessions),
        "SFBT": SfbtTherapyAgent(llm, session_store=sessions),
    }
    dialogue_line = DialogueLine(host, session_store=sessions)
    decision_line = DecisionLine(
        safety,
        session_store=sessions,
        therapy_decider=TherapyDecider(llm),
        therapy_agents=therapy_agents,
        affect_engine=AffectLabelingEngine(llm, session_store=sessions),
        pmr_engine=ProgressiveMuscleRelaxationEngine(llm, session_store=sessions),
        grounding_engine=GroundingEngine(llm, session_store=sessions),
    )
    settlement = SettlementLine(llm, session_store=sessions, profile_store=profiles)
    return Scheduler(
        dialogue_line=dialogue_line,
        decision_line=decision_line,
        settlement_line=settlement,
        session_store=sessions,
        profile_store=profiles,
    )
