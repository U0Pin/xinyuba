"""决策线 → 调度器的信号定义（新架构）。

信号只在轮边界由调度器（代码）应用，下一轮生效。信号携带的数据 = apply()
消费的 + 值得随状态机留痕的（如 EndTherapy.reason：它写进 state.last_therapy，
让"为什么停"成为可携带的事实，供 Host 交接与观察）。其余决策上下文
由决策线 app_log 记录（safety_done / therapy_decide_done / utility_update）。
"""

from dataclasses import dataclass

from src.core.state import SessionState, TherapyProgress, owner_for


@dataclass(frozen=True)
class SwitchToTherapy:
    """切换到疗法 Agent（由疗法决策产生）。"""
    therapy: str  # "CBT" | "ACT" | "DBT" | "MI" | "SFBT"
    start_seq: int = 0  # 疗程起始流水序号（决策线侧读取流水末尾计算）

    def apply(self, state: SessionState) -> None:
        state.owner = owner_for(self.therapy)
        # step_index=-1：下一轮编排时按评估路由决定初始步骤
        state.therapy = TherapyProgress(name=self.therapy, start_seq=self.start_seq)
        state.affect_labeling = None  # 情绪支持让位于当前疗法流程
        state.pmr = None  # PMR 同属情绪支持，让位于当前疗法流程
        state.grounding = None  # Grounding 同属情绪支持，让位于当前疗法流程


@dataclass(frozen=True)
class EndTherapy:
    """结束疗程（效用裁决、轮数上限或未知疗法时产生）。

    reason: "stagnation" | "regression" | "round_cap" | "unknown_therapy"
    """
    reason: str = ""

    def apply(self, state: SessionState) -> None:
        if state.therapy is not None:
            state.last_therapy = {
                "name": state.therapy.name,
                "rounds": state.therapy.rounds,
                "ended_reason": self.reason,
            }
        state.owner = "daily"
        state.therapy = None


@dataclass(frozen=True)
class CrisisDetected:
    """检测到 CRISIS：下一轮起危机应对（回复由 Host 的危机模板生成）。"""

    def apply(self, state: SessionState) -> None:
        if state.therapy is not None:
            # 危机同样终止疗程，并把原因留痕（与 EndTherapy 同一交接块）
            state.last_therapy = {
                "name": state.therapy.name,
                "rounds": state.therapy.rounds,
                "ended_reason": "crisis",
            }
        state.crisis = True
        state.owner = "daily"
        state.therapy = None         # 警示期间疗法不工作
        state.affect_labeling = None  # 情绪支持让位于安全流程
        state.pmr = None             # PMR 让位于安全流程
        state.grounding = None       # Grounding 让位于安全流程


@dataclass(frozen=True)
class CrisisCleared:
    """危机解除：恢复时机由决策线（安全决策）决定。"""

    def apply(self, state: SessionState) -> None:
        state.crisis = False


Signal = SwitchToTherapy | EndTherapy | CrisisDetected | CrisisCleared
