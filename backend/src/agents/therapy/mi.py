"""MI 疗法 Agent（Phase 3）。

基本流程（阶段式）：
    五维评估 → 高矛盾/无 change talk → explore；有 change talk/低自我效能 → evocation；
    高准备度+强 change talk → commitment → 干预 → 步骤判断；
    完成 = commitment 达成（改变计划落地）；准备度回落 → 回 explore（不跳级）。
"""

from src.agents.therapy.base import FlowStep, TherapyAgentBase

_MI_INDEX = {"explore": 0, "evocation": 1, "commitment": 2}


class MiTherapyAgent(TherapyAgentBase):
    therapy_name = "MI"
    assessment_skill = "mi_state_assessment"

    FLOW = [
        FlowStep("explore", skills=["mi_explore"]),
        FlowStep("evocation", skills=["mi_evocation"]),
        FlowStep("commitment", skills=["mi_commitment"]),
    ]

    def initial_step_index(self, assessment):
        a = assessment or {}
        ct = (a.get("change_talk") or {}).get("index", 0)
        st = (a.get("sustain_talk") or {}).get("index", 0)
        cr = (a.get("change_readiness") or {}).get("index", 0)
        se = (a.get("self_efficacy") or {}).get("index", 0)
        amb = (a.get("ambivalence") or {}).get("index", 0)
        # 高 sustain + 低 change talk → explore（不 push）
        if st >= 0.6 and ct < 0.35:
            return _MI_INDEX["explore"]
        # 高准备度 + 强 change talk → commitment
        if cr >= 0.75 and ct >= 0.6:
            return _MI_INDEX["commitment"]
        # 强矛盾且 change talk 尚弱 → explore（先探索矛盾）
        if amb >= 0.5 and ct < 0.35:
            return _MI_INDEX["explore"]
        # 有 change talk（弱及以上）且自我效能低 → evocation
        if ct >= 0.35 or (ct >= 0.10 and se < 0.5):
            return _MI_INDEX["evocation"]
        # 默认：按评估的推荐过程第一个
        for rp in a.get("recommended_processes", []):
            if rp.get("process") in _MI_INDEX:
                return _MI_INDEX[rp["process"]]
        return 0

    def assessment_block(self, assessment, products):
        a = assessment or {}
        return {
            "ambivalence": a.get("ambivalence", {}),
            "sustain_talk": a.get("sustain_talk", {}),
            "change_talk": a.get("change_talk", {}),
            "self_efficacy": a.get("self_efficacy", {}),
            "change_readiness": a.get("change_readiness", {}),
        }
