"""定量边际效用评估的单元测试（纯函数，无 LLM 依赖）。

覆盖：state_score 五法提取 / compute_record 记账语义 / decide 四条规则
（冷启动、倒退、停滞、噪声保护）/ 端到端账本回放。
"""

from src.core.marginal_utility import (
    MarginalUtilityConfig,
    UtilityRecord,
    compute_record,
    decide,
    seen_techniques,
    state_score,
)

CFG = MarginalUtilityConfig()


def act_assessment(pf):
    return {"psychological_flexibility": pf}


def skill(tech_name):
    return {"_skill_name": "x", "technique": {"name": tech_name}}


def run_ledger(actions, scores, techniques, cfg=CFG):
    """驱动一条完整效用账（模拟 _orchestrate 的调用序）。"""
    history = []
    prev_score = None
    for i, (action, score, tech) in enumerate(
            zip(actions, scores, techniques, strict=True), start=1):
        s = score if score is not None else prev_score  # 稀疏信号携带
        prev_score = s
        rec = compute_record(
            round_no=i, judgment_action=action,
            skill_output=skill(tech) if tech else None,
            score=s,
            prev=UtilityRecord(**history[-1]) if history else None,
            history=history, cfg=cfg,
        )
        history.append(rec.to_dict())
    return history


# ═══ state_score ═══

class TestStateScore:
    def test_act_positive_direct(self):
        assert state_score("ACT", act_assessment(0.62), None) == 0.62

    def test_dbt_mi_sfbt_inverted(self):
        assert state_score("DBT", {"dbt_intervention_priority": 0.3}, None) == 0.7
        assert state_score("MI", {"mi_intervention_need": 0.45}, None) == 0.55
        assert state_score("SFBT", {"sfbt_intervention_need": 0.2}, None) == 0.8

    def test_cbt_from_classifications_mean_confidence(self):
        out = {"classifications": [
            {"distortions": [{"type": "a", "confidence": 0.8},
                             {"type": "b", "confidence": 0.6}]},
        ]}
        assert state_score("CBT", None, out) == 0.3  # 1 − mean(0.8, 0.6)

    def test_cbt_no_classifications_returns_none(self):
        assert state_score("CBT", None, {"classifications": []}) is None

    def test_missing_assessment_returns_none(self):
        assert state_score("ACT", None, None) is None
        assert state_score("UNKNOWN", {"anything": 1}, None) is None

    def test_out_of_range_clamped(self):
        assert state_score("ACT", act_assessment(1.7), None) == 1.0
        assert state_score("DBT", {"dbt_intervention_priority": -0.5}, None) == 1.0


# ═══ compute_record ═══

class TestComputeRecord:
    def test_first_round_no_delta_but_gain(self):
        rec = compute_record(round_no=1, judgment_action="advance",
                             skill_output=skill("defusion"), score=0.4,
                             prev=None, history=[], cfg=CFG)
        assert rec.state_delta is None
        assert rec.novelty == 1.0
        assert rec.gain is True

    def test_repeated_technique_zero_novelty(self):
        history = [{"technique": "defusion"}]
        rec = compute_record(round_no=2, judgment_action="stay",
                             skill_output=skill("defusion"), score=0.4,
                             prev=UtilityRecord(round=1, action="advance", progress=1.0,
                                                state_score=0.4),
                             history=history, cfg=CFG)
        assert rec.novelty == 0.0 and rec.state_delta == 0.0
        assert rec.gain is False  # stay + 重复技术 + 状态不变 = 无收益轮

    def test_judgment_only_step_novelty_none_renormalized(self):
        rec = compute_record(round_no=3, judgment_action="stay",
                             skill_output=None, score=0.5,
                             prev=UtilityRecord(round=2, action="advance", progress=1.0,
                                                state_score=0.45),
                             history=[], cfg=CFG)
        assert rec.novelty is None
        assert rec.utility == round(CFG.w_state * 0.05 / (CFG.w_progress + CFG.w_state), 3)

    def test_complete_gets_milestone_bonus(self):
        a = compute_record(round_no=1, judgment_action="complete", skill_output=None,
                           score=None, prev=None, history=[], cfg=CFG)
        assert a.progress == 1.2


# ═══ decide 四规则 ═══

class TestDecide:
    def _rec(self, gain=True, delta=None, round_no=1):
        return {"round": round_no, "gain": gain, "state_delta": delta,
                "utility": 0.5 if gain else 0.0, "ewma": 0.3}

    def test_cold_start_never_stops(self):
        h = [self._rec(gain=False, round_no=i) for i in range(1, CFG.min_rounds)]
        assert decide(h, CFG).stop is False

    def test_stagnation_after_two_zero_gains(self):
        h = [self._rec(gain=True, round_no=1), self._rec(gain=True, round_no=2),
             self._rec(gain=False, round_no=3), self._rec(gain=False, round_no=4)]
        assert decide(h, CFG).rule == "stagnation"

    def test_one_bad_round_between_gains_not_stop(self):
        h = [self._rec(gain=True, round_no=1), self._rec(gain=True, round_no=2),
             self._rec(gain=False, round_no=3), self._rec(gain=True, round_no=4)]
        assert decide(h, CFG).stop is False  # 噪声单轮保护

    def test_hard_regression_stops_immediately(self):
        h = [self._rec(round_no=1), self._rec(round_no=2),
             self._rec(gain=True, delta=-0.30, round_no=3)]
        assert decide(h, CFG).rule == "regression"

    def test_pairwise_soft_regression_stops(self):
        h = [self._rec(gain=True, round_no=1), self._rec(gain=True, delta=-0.15, round_no=2),
             self._rec(gain=True, delta=-0.13, round_no=3)]
        assert decide(h, CFG).rule == "regression"

    def test_single_soft_regression_waits(self):
        h = [self._rec(gain=True, round_no=1), self._rec(gain=True, round_no=2),
             self._rec(gain=True, delta=-0.15, round_no=3)]
        assert decide(h, CFG).stop is False  # 未达 hard、不够两轮配对

    def test_empty_history(self):
        v = decide([], CFG)
        assert v.stop is False and v.rule is None


# ═══ 端到端账本回放 ═══

class TestLedgerReplay:
    def test_steady_improvement_never_stops(self):
        """推进+新技术+状态上扬的健康疗程：20 轮不断。"""
        h = run_ledger(
            actions=["advance"] * 20,
            scores=[0.30 + 0.02 * i for i in range(20)],
            techniques=[f"t{i % 4}" for i in range(20)],
        )
        for i in range(3, 21):
            assert decide(h[:i], CFG).stop is False, f"round {i} 不该停"

    def test_deep_work_stay_rounds_survive(self):
        """关键回归用例：流程 stay 但状态持续改善（ΔS≈0.06）→ 不误杀。
        （聚合阈值版会在这里误停。）"""
        h = run_ledger(
            actions=["advance", "stay", "stay", "stay", "stay"],
            scores=[0.30, 0.36, 0.42, 0.48, 0.54],
            techniques=["defusion", "defusion", "defusion", "defusion", "defusion"],
        )
        assert decide(h, CFG).stop is False

    def test_true_stagnation_stops_at_round_four(self):
        """推进一轮后彻底停滞（重复技术、状态平、stay）→ 第 4 轮停。"""
        h = run_ledger(
            actions=["advance", "advance", "stay", "stay"],
            scores=[0.30, 0.30, 0.30, 0.30],
            techniques=["a", "b", "b", "b"],
        )
        assert decide(h[:3], CFG).stop is False
        assert decide(h[:4], CFG).rule == "stagnation"

    def test_seen_techniques_collects_names(self):
        h = run_ledger(actions=["advance", "advance"], scores=[0.3, 0.35],
                       techniques=["a", "b"])
        assert seen_techniques(h) == {"a", "b"}
