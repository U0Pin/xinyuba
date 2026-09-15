"""定量边际效用评估（纯函数模块，与 act_state.py 同族：公式裁决、LLM 供料）。

设计（2026-09 定稿）：疗法是否值得继续，不再每轮问一次 LLM，
而是给每轮记一笔**效用账**——

    U(t) = w_progress · progress(t)     流程推进收益（步骤判断动作）
         + w_state     · ΔS(t)          状态改善收益（疗法状态标量环比）
         + w_novelty   · novelty(t)     新信息收益（技术是否首次出现）

U/EWMA 是观测量（日志、webui 曲线、调参回放）；**裁决用两条可解释规则**
（聚合阈值会误伤"流程暂停但状态稳步改善"的深度轮，故不做判据）：

    冷启动：账本不足 min_rounds 条 → 永不判停（无环比可言）；
    倒退：  ΔS ≤ −theta_hard（单轮显著恶化），或连续两轮 ΔS ≤ −theta
            → 停，rule="regression"；
    停滞：  连续 stagnation_rounds 轮"无收益轮" → 停，rule="stagnation"；
            收益轮 = 有流程推进（progress>0）或有新技术（novelty=1）
                     或状态改善 ≥ zero_round_delta_min 的轮；
    否则持续。硬上限（THERAPY_MAX_ROUNDS）与危机不经此处，由调度器兜底。

基础疗法状态标量 S(t) ∈ [0,1]（越大越好），全部来自评估技能里
**已存在的后端公式输出**，不新增任何 LLM 调用：
    ACT  psychological_flexibility（正向，直接用）
    DBT  1 − dbt_intervention_priority
    MI   1 − mi_intervention_need
    SFBT 1 − sfbt_intervention_need
    CBT  1 − 本轮扭曲分类的平均 confidence（信念强度↓ = 解离度↑；
         分类轮才有的稀疏信号，缺轮次由调用方携带上一轮值）

账本本身（list[dict]）存在 TherapyProgress.utility_history，随疗程存亡；
本模块只做纯计算，读写在 core/scheduler.py。
"""

from dataclasses import dataclass
from typing import Optional


# ── 配置（调参唯一点位） ────────────────────────────────────────────

@dataclass(frozen=True)
class MarginalUtilityConfig:
    w_progress: float = 0.35        # 效用账：流程推进权重（观测量）
    w_state: float = 0.40           # 效用账：状态改善权重
    w_novelty: float = 0.25         # 效用账：新信息权重
    theta: float = 0.12             # 倒退：单轮阈值（连续两轮制）
    theta_hard: float = 0.24        # 倒退：单轮即时停的显著恶化阈值
    zero_round_delta_min: float = 0.03  # 收益轮判定：状态改善至少多少算有收益
    min_rounds: int = 3             # 冷启动保护
    stagnation_rounds: int = 2      # 连续 N 轮无收益判停
    ewma_alpha: float = 0.5         # EWMA 平滑（仅观测）


# ── 效用记录（账本条目，存 TherapyProgress.utility_history） ────────

@dataclass
class UtilityRecord:
    round: int
    action: str                          # 本轮步骤判断动作（stay/advance/go_to/complete）
    progress: float                      # 动作 → 收益映射
    state_score: Optional[float] = None  # S(t)
    state_delta: Optional[float] = None  # ΔS(t)
    novelty: Optional[float] = None      # 1 新技术 / 0 重复 / None 本轮无技能执行
    technique: Optional[str] = None
    utility: float = 0.0                 # 三项加权（缺项按剩余权重重归一）
    ewma: float = 0.0
    gain: bool = True                    # 是否"收益轮"（停滞判定的最小单元）

    def to_dict(self) -> dict:
        return dict(self.__dict__)


# 步骤判断动作 → 流程推进收益（complete 带里程碑加成）
_PROGRESS_BY_ACTION = {"advance": 1.0, "complete": 1.2, "go_to": 0.6, "stay": 0.0}


# ── 状态标量 ────────────────────────────────────────────────────────

def state_score(therapy_name: str, assessment: Optional[dict],
                skill_output: Optional[dict]) -> Optional[float]:
    """从本轮分析产物提取疗法状态标量 S ∈ [0,1]；无信号返回 None。"""
    if therapy_name == "ACT" and assessment:
        return _clamp01(assessment.get("psychological_flexibility"))
    if therapy_name == "DBT" and assessment:
        p = _clamp01(assessment.get("dbt_intervention_priority"))
        return None if p is None else round(1 - p, 3)
    if therapy_name == "MI" and assessment:
        n = _clamp01(assessment.get("mi_intervention_need"))
        return None if n is None else round(1 - n, 3)
    if therapy_name == "SFBT" and assessment:
        n = _clamp01(assessment.get("sfbt_intervention_need"))
        return None if n is None else round(1 - n, 3)
    if therapy_name == "CBT":
        # 只信本轮分类结果（products 是历史累积，均值不动 = 假性平稳）
        confs = [d.get("confidence")
                 for c in ((skill_output or {}).get("classifications") or [])
                 for d in (c.get("distortions") or [])
                 if isinstance(d.get("confidence"), (int, float))]
        if confs:
            return round(1 - sum(confs) / len(confs), 3)
    return None


def seen_techniques(history: list) -> set:
    """账本中出现过的技术名（novelty 判定用）。"""
    return {r.get("technique") for r in history if r.get("technique")}


# ── 记账 ────────────────────────────────────────────────────────────

def compute_record(*, round_no: int, judgment_action: str,
                   skill_output: Optional[dict], score: Optional[float],
                   prev: Optional[UtilityRecord], history: list,
                   cfg: MarginalUtilityConfig = MarginalUtilityConfig()) -> UtilityRecord:
    """记一笔本轮效用账。score=None 时由调用方先行携带上一轮值（CBT 稀疏信号）。"""
    progress = _PROGRESS_BY_ACTION.get(judgment_action, 0.0)

    technique = ((skill_output or {}).get("technique") or {}).get("name") or None
    if skill_output is None:
        novelty = None                                   # 本轮无技能执行（judgment_only 步）
    else:
        novelty = 0.0 if technique in seen_techniques(history) else 1.0

    delta = None
    if score is not None and prev is not None and prev.state_score is not None:
        delta = round(score - prev.state_score, 3)

    # 观测效用：缺项剔除并按剩余权重重归一（冷启动/稀疏信号的公平处理）
    terms = [(progress, cfg.w_progress)]
    if delta is not None:
        terms.append((_clampn1(delta), cfg.w_state))
    if novelty is not None:
        terms.append((novelty, cfg.w_novelty))
    utility = round(
        sum(value * weight for value, weight in terms)
        / sum(weight for _, weight in terms),
        3,
    )

    prev_ewma = prev.ewma if prev is not None else utility
    ewma = round(cfg.ewma_alpha * utility + (1 - cfg.ewma_alpha) * prev_ewma, 3)

    gain = (progress > 0
            or novelty == 1.0
            or (delta is not None and delta >= cfg.zero_round_delta_min))

    return UtilityRecord(
        round=round_no, action=judgment_action, progress=progress,
        state_score=score, state_delta=delta, novelty=novelty,
        technique=technique, utility=utility, ewma=ewma, gain=gain,
    )


# ── 裁决 ────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    """裁决结论；rule=None 表示继续。stop 与 rule 一致（stop=(rule is not None)）。"""
    stop: bool
    rule: Optional[str]
    utility: float = 0.0
    ewma: float = 0.0


def decide(history: list, cfg: MarginalUtilityConfig = MarginalUtilityConfig()) -> Verdict:
    """在轮边界对账本做持续/停止裁决（纯函数，确定性，可单测回放）。"""
    if not history:
        return Verdict(stop=False, rule=None)
    latest = history[-1]
    u, ewma = float(latest.get("utility", 0.0)), float(latest.get("ewma", 0.0))

    if len(history) < cfg.min_rounds:
        return Verdict(stop=False, rule=None, utility=u, ewma=ewma)  # 冷启动保护

    # 倒退：单轮显著恶化，或连续两轮恶化
    d = latest.get("state_delta")
    if d is not None and d <= -cfg.theta_hard:
        return Verdict(stop=True, rule="regression", utility=u, ewma=ewma)
    if len(history) >= 2:
        d_prev = history[-2].get("state_delta")
        if (d is not None and d_prev is not None
                and d <= -cfg.theta and d_prev <= -cfg.theta):
            return Verdict(stop=True, rule="regression", utility=u, ewma=ewma)

    # 停滞：连续 N 轮无收益
    tail = history[-cfg.stagnation_rounds:]
    if len(tail) >= cfg.stagnation_rounds and all(not r.get("gain", True) for r in tail):
        return Verdict(stop=True, rule="stagnation", utility=u, ewma=ewma)

    return Verdict(stop=False, rule=None, utility=u, ewma=ewma)


# ── 小工具 ─────────────────────────────────────────────────────────

def _clamp01(x) -> Optional[float]:
    if x is None:
        return None
    return round(max(0.0, min(1.0, float(x))), 3)


def _clampn1(x: float) -> float:
    return max(-1.0, min(1.2, x))
