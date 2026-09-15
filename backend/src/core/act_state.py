"""ACT（接纳承诺疗法）状态机。

定义 ACT 干预技能读写的五维心理灵活性维度。本模块是这些类型的唯一定义处，
技能与测试直接 `from src.core.act_state import ...`（旧兼容路径
`src.core.state` 已随遗留 dataclass 一并退役）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FusionState(str, Enum):
    """认知融合（fusion）状态机的离散档位。

    数值范围与阈值见 `ACTStateVector.compute_fusion_state`。
    """
    CF_OBSERVING = "cf_observing"           # fusion_index ∈ [0.0, 0.25) — 旁观想法
    CF_HOOKABLE = "cf_hookable"             # fusion_index ∈ [0.25, 0.45) — 想法有牵引力但可抽离
    CF_HOOKED = "cf_hooked"                 # fusion_index ∈ [0.45, 0.65) — 想法被体验为事实
    CF_FUSED = "cf_fused"                   # fusion_index ∈ [0.65, 0.85) — 自我与想法合一
    CF_IDENTITY_FUSION = "cf_identity_fusion"  # fusion_index ∈ [0.85, 1.0] — 想法 = 自我定义


class AvoidanceState(str, Enum):
    """经验性回避（experiential avoidance）状态机的离散档位。"""
    EA_WILLING = "ea_willing"               # avoidance_index ∈ [0.0, 0.20) — 愿意面对
    EA_HESITANT = "ea_hesitant"             # avoidance_index ∈ [0.20, 0.40) — 犹豫
    EA_AVOIDING = "ea_avoiding"             # avoidance_index ∈ [0.40, 0.65) — 主动回避
    EA_RIGID_CONTROL = "ea_rigid_control"   # avoidance_index ∈ [0.65, 0.85) — 僵化控制
    EA_DISSOCIATING = "ea_dissociating"     # avoidance_index ∈ [0.85, 1.0] — 解离风险


class OpennessState(str, Enum):
    """情绪开放度（emotional openness）状态机的离散档位。"""
    EO_OPEN = "eo_open"                     # openness_index ∈ [0.70, 1.0] — 开放表达
    EO_RECEPTIVE = "eo_receptive"           # openness_index ∈ [0.45, 0.70) — 愿意接收
    EO_GUARDED = "eo_guarded"               # openness_index ∈ [0.25, 0.45) — 有所防备
    EO_DEFENDED = "eo_defended"             # openness_index ∈ [0.10, 0.25) — 高度防御
    EO_SHUTDOWN = "eo_shutdown"             # openness_index ∈ [0.0, 0.10] — 关闭、不可达


class FlexibilityLevel(str, Enum):
    """心理灵活性（psychological flexibility）的五档分级。"""
    PF_FLEXIBLE = "pf_flexible"             # flexibility ∈ [0.75, 1.0]
    PF_ADAPTIVE = "pf_adaptive"             # flexibility ∈ [0.50, 0.75)
    PF_RIGID = "pf_rigid"                   # flexibility ∈ [0.30, 0.50)
    PF_BRITTLE = "pf_brittle"               # flexibility ∈ [0.15, 0.30)
    PF_FROZEN = "pf_frozen"                 # flexibility ∈ [0.0, 0.15)


class EscalationLevel(int, Enum):
    """情绪升级阶梯（int int Enum，便于数值比较）。

    与 `OpennessState` 等 str Enum 不同：这里用 int 是为了让上层
    能做大小比较和 `>= threshold` 判断。
    """
    BASELINE = 0                            # intensity < 0.30
    ACTIVATED = 1                           # intensity ∈ [0.30, 0.55)
    ESCALATING = 2                          # intensity ∈ [0.55, 0.75)
    HIGHLY_ESCALATED = 3                    # intensity ∈ [0.75, 0.90)
    CRISIS_BOUNDARY = 4                     # intensity >= 0.90


class InteractionStyle(str, Enum):
    """对话风格（adaptation 时用），与 LLM 输出的 adaptation 字段对齐。"""
    EXPLORATORY = "exploratory"
    SUPPORTIVE = "supportive"
    DIRECTIVE = "directive"
    ANCHORING = "anchoring"


@dataclass
class ACTStateVector:
    """每轮的 ACT 状态向量，承载五个心理灵活性维度及派生量。

    - 五个主索引（fusion/avoidance/openness/alignment/activation）∈ [0, 1]
    - 每个维度配一个 confidence（∈ [0, 1]）和 evidence（用户语言线索）
    - 离散状态由 `compute_*_state` 从主索引映射而来
    - `psychological_flexibility` 和 `emotional_intensity` 由后端公式
      计算，不由 LLM 生成（避免 LLM 在压力下高估自身灵活性）
    """

    # ── 主索引 ∈ [0, 1] ─────────────────────────────────────────
    fusion_index: float = 0.0
    avoidance_index: float = 0.0
    openness_index: float = 0.5
    alignment_index: float = 0.5
    activation_index: float = 0.5

    # ── 每个维度的置信度 ∈ [0, 1] ───────────────────────────────
    fusion_confidence: float = 0.0
    avoidance_confidence: float = 0.0
    openness_confidence: float = 0.0
    alignment_confidence: float = 0.0
    activation_confidence: float = 0.0

    # ── 语言证据：用户原话中触发该维度的具体片段 ────────────────
    fusion_evidence: list[str] = field(default_factory=list)
    avoidance_evidence: list[str] = field(default_factory=list)
    openness_evidence: list[str] = field(default_factory=list)
    values_mentioned: list[str] = field(default_factory=list)

    # ── 离散档位：由对应 compute_*_state 写入 ───────────────────
    fusion_state: FusionState = FusionState.CF_OBSERVING
    avoidance_state: AvoidanceState = AvoidanceState.EA_WILLING
    openness_state: OpennessState = OpennessState.EO_RECEPTIVE
    flexibility_level: FlexibilityLevel = FlexibilityLevel.PF_ADAPTIVE

    # ── 派生量 ─────────────────────────────────────────────────
    psychological_flexibility: float = 0.5
    emotional_intensity: float = 0.0
    escalation_level: EscalationLevel = EscalationLevel.BASELINE

    # ── 评估元数据 ─────────────────────────────────────────────
    overall_trend: str = "stable"           # improving | stable | worsening
    primary_concern: str = ""               # 当前最突出维度
    should_intervene: bool = False
    recommended_processes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fusion_index": self.fusion_index,
            "avoidance_index": self.avoidance_index,
            "openness_index": self.openness_index,
            "alignment_index": self.alignment_index,
            "activation_index": self.activation_index,
            "fusion_confidence": self.fusion_confidence,
            "avoidance_confidence": self.avoidance_confidence,
            "openness_confidence": self.openness_confidence,
            "alignment_confidence": self.alignment_confidence,
            "activation_confidence": self.activation_confidence,
            "fusion_state": self.fusion_state.value,
            "avoidance_state": self.avoidance_state.value,
            "openness_state": self.openness_state.value,
            "flexibility_level": self.flexibility_level.value,
            "psychological_flexibility": self.psychological_flexibility,
            "emotional_intensity": self.emotional_intensity,
            "escalation_level": self.escalation_level.name.lower(),
            "overall_trend": self.overall_trend,
            "primary_concern": self.primary_concern,
            "should_intervene": self.should_intervene,
            "recommended_processes": self.recommended_processes,
        }

    @staticmethod
    def compute_fusion_state(index: float) -> FusionState:
        """把连续 fusion 索引映射到五个离散档位。"""
        if index < 0.25:
            return FusionState.CF_OBSERVING
        elif index < 0.45:
            return FusionState.CF_HOOKABLE
        elif index < 0.65:
            return FusionState.CF_HOOKED
        elif index < 0.85:
            return FusionState.CF_FUSED
        else:
            return FusionState.CF_IDENTITY_FUSION

    @staticmethod
    def compute_avoidance_state(index: float) -> AvoidanceState:
        """把连续 avoidance 索引映射到五个离散档位。"""
        if index < 0.20:
            return AvoidanceState.EA_WILLING
        elif index < 0.40:
            return AvoidanceState.EA_HESITANT
        elif index < 0.65:
            return AvoidanceState.EA_AVOIDING
        elif index < 0.85:
            return AvoidanceState.EA_RIGID_CONTROL
        else:
            return AvoidanceState.EA_DISSOCIATING

    @staticmethod
    def compute_openness_state(index: float) -> OpennessState:
        """把连续 openness 索引映射到五个离散档位（高=开放）。"""
        if index >= 0.70:
            return OpennessState.EO_OPEN
        elif index >= 0.45:
            return OpennessState.EO_RECEPTIVE
        elif index >= 0.25:
            return OpennessState.EO_GUARDED
        elif index >= 0.10:
            return OpennessState.EO_DEFENDED
        else:
            return OpennessState.EO_SHUTDOWN

    @staticmethod
    def compute_flexibility_level(pf: float) -> FlexibilityLevel:
        """把连续心理灵活性分数映射到五档分级。"""
        if pf >= 0.75:
            return FlexibilityLevel.PF_FLEXIBLE
        elif pf >= 0.50:
            return FlexibilityLevel.PF_ADAPTIVE
        elif pf >= 0.30:
            return FlexibilityLevel.PF_RIGID
        elif pf >= 0.15:
            return FlexibilityLevel.PF_BRITTLE
        else:
            return FlexibilityLevel.PF_FROZEN

    @staticmethod
    def compute_escalation(intensity: float) -> EscalationLevel:
        """把连续情绪强度映射到五档升级阶梯。"""
        if intensity >= 0.90:
            return EscalationLevel.CRISIS_BOUNDARY
        elif intensity >= 0.75:
            return EscalationLevel.HIGHLY_ESCALATED
        elif intensity >= 0.55:
            return EscalationLevel.ESCALATING
        elif intensity >= 0.30:
            return EscalationLevel.ACTIVATED
        else:
            return EscalationLevel.BASELINE

    @staticmethod
    def compute_flexibility(fusion: float, avoidance: float, openness: float,
                            alignment: float, activation: float) -> float:
        """后端公式计算 psychological_flexibility — 不由 LLM 生成。

        权重设计：
        - 0.35 减 fusion（高融合 = 低灵活性）
        - 0.25 减 avoidance（高回避 = 低灵活性）
        - 0.20 加 openness（开放 = 灵活性）
        - 0.10 加 alignment（与价值对齐）
        - 0.10 加 activation（行为激活度）
        """
        return round(
            0.35 * (1 - fusion)
            + 0.25 * (1 - avoidance)
            + 0.20 * openness
            + 0.10 * alignment
            + 0.10 * activation,
            3
        )
