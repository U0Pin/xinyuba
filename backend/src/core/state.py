"""调度状态核心模型：schema 与词汇的唯一权威定义处。

`state.json` 是调度层的存盘契约（见 ARCHITECTURE.md「单写者纪律」）：

- 唯一写入者：Scheduler._apply_signals → `SessionState.to_dict()`；
  其余各线只经 `TurnContext.state` 读取快照，更新一律走 DecisionResult 回传、
  轮边界生效；
- `SessionStore.DEFAULT_STATE` 保持最小起步骨架（新建/reset 时写），
  调度器首轮保存即产出完整规范骨架；所有消费方经 `from_dict` 读，
  缺键取默认值，因此新旧文件兼容；
- `affect_labeling` 子状态仅由 AffectLabelingEngine 书写与理解（dict 透传，
  本模型只保证"存在且可清空"）；
- 疗法名词汇：THERAPIES；owner ↔ 疗法协议：owner_for / therapy_key_for_owner。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Optional

# ── 疗法词汇与 owner 协议（"therapy_cbt" ↔ "CBT"） ─────────────────
#
# Host 架构（2026-09）起 owner 不再决定"谁说话"（永远是 Host），
# 降级为**审计标签**：流水条目记录当轮生效的接管上下文、LLM 记账的
# agent 标签、webui/评测的分组依据。写入值与原设计逐字一致。

THERAPIES = ("CBT", "ACT", "DBT", "MI", "SFBT")

_OWNER_PREFIX = "therapy_"


def owner_for(therapy_key: str) -> str:
    """疗法键（"CBT"）→ 接管上下文标识（"therapy_cbt"）。"""
    return f"{_OWNER_PREFIX}{therapy_key.lower()}"


def therapy_key_for_owner(owner: str) -> Optional[str]:
    """接管标识 → 疗法键；非疗法 owner（daily 等）返回 None。"""
    if not owner.startswith(_OWNER_PREFIX):
        return None
    return owner[len(_OWNER_PREFIX):].upper()


# ── 风险档位 ───────────────────────────────────────────────────────

class RiskState(str, Enum):
    """安全决策的 5 级风险阶梯。str mixin：JSON 序列化即字符串。

    __str__ 取 str 版本，保证 prompt 插值输出 "CRISIS" 而非
    "RiskState.CRISIS"（prompt 字节面被黄金快照冻结）。
    """
    SAFE = "SAFE"
    LOW_RISK = "LOW_RISK"
    MEDIUM_RISK = "MEDIUM_RISK"
    HIGH_RISK = "HIGH_RISK"
    CRISIS = "CRISIS"

    __str__ = str.__str__


# ── 疗程进度 ───────────────────────────────────────────────────────

@dataclass
class TherapyProgress:
    """一次疗程的进度指针（决策线编排拥有全部读写）。

    step_index = -1 表示"尚未定初始步骤"：下一轮编排按评估路由决定。
    visited 记录已达成主步骤名（ACT 的"推荐过程依次达成"用）。
    """
    name: str = ""
    start_seq: int = 0
    rounds: int = 0
    step_index: int = -1
    branch: Optional[str] = None
    basic_flow_complete: bool = False
    visited: list = field(default_factory=list)
    utility_history: list = field(default_factory=list)
    # ^ 边际效用账本（每轮一条 UtilityRecord.to_dict()，见 core/marginal_utility.py）；
    #   随疗程存亡，B1 起承担持续/停止的全程裁决记录。

    @classmethod
    def from_dict(cls, d: dict) -> "TherapyProgress":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)

    def copy(self) -> "TherapyProgress":
        """深一层拷贝（visited 复制）。决策线只在轮边界回传更新，
        不得原地修改 ctx 快照——并行运行的对话线可能随时读它。"""
        return TherapyProgress.from_dict(self.to_dict())


# ── 调度状态（state.json 的模型） ──────────────────────────────────

@dataclass
class SessionState:
    """调度状态：owner / 危机 / 风险 / 疗程 / 触发计数 / 情绪支持子状态。"""
    owner: str = "daily"
    crisis: bool = False
    risk_level: RiskState = RiskState.SAFE
    therapy: Optional[TherapyProgress] = None
    last_therapy: Optional[dict] = None
    last_therapy_check_seq: int = 0
    pending_user_tokens: int = 0
    affect_labeling: Optional[dict] = None
    pmr: Optional[dict] = None  # PMR 子状态（见 src/agents/progressive_muscle_relaxation.py）
    grounding: Optional[dict] = None  # Grounding 子状态（见 src/agents/grounding.py）

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        """宽容解析：旧文件缺键取默认；未知键忽略；非法风险档回落 SAFE。"""
        try:
            risk = RiskState(d.get("risk_level", "SAFE"))
        except ValueError:
            risk = RiskState.SAFE
        therapy = d.get("therapy")
        return cls(
            owner=d.get("owner", "daily"),
            crisis=bool(d.get("crisis", False)),
            risk_level=risk,
            therapy=TherapyProgress.from_dict(therapy) if therapy else None,
            last_therapy=d.get("last_therapy"),
            last_therapy_check_seq=int(d.get("last_therapy_check_seq", 0)),
            pending_user_tokens=int(d.get("pending_user_tokens", 0)),
            affect_labeling=d.get("affect_labeling"),
            pmr=d.get("pmr"),
            grounding=d.get("grounding"),
        )

    def to_dict(self) -> dict:
        return {
            "owner": self.owner,
            "crisis": self.crisis,
            "risk_level": self.risk_level.value,
            "therapy": self.therapy.to_dict() if self.therapy else None,
            "last_therapy": self.last_therapy,
            "last_therapy_check_seq": self.last_therapy_check_seq,
            "pending_user_tokens": self.pending_user_tokens,
            "affect_labeling": self.affect_labeling,
            "pmr": self.pmr,
            "grounding": self.grounding,
        }

    def copy(self) -> "SessionState":
        """快照拷贝（therapy / affect_labeling 一并复制）。

        轮边界应用更新时必须从副本起步——`TurnContext` 是与并行运行的
        对话线共享的只读快照，原地修改会破坏"信号下一轮生效"的时序契约。
        """
        return SessionState.from_dict(self.to_dict())


# ── 编排结果（orchestration.json 的模型） ──────────────────────────

@dataclass
class Orchestration:
    """一轮疗程编排的产出信封（决策线写、对话线下一轮读）。

    载荷字段（assessment / skill_output / step_judgment / utility_eval /
    products）是 LLM 自由结构或公式记录，保持 dict；本模型只固定信封的键集与默认值。
    """
    updated_seq: int = 0
    therapy: str = ""
    current_step: str = ""
    next_step: str = ""
    step_judgment: dict = field(default_factory=dict)
    assessment: Optional[dict] = None
    skill_output: Optional[dict] = None
    products: dict = field(default_factory=dict)
    utility_eval: Optional[dict] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Orchestration":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)
