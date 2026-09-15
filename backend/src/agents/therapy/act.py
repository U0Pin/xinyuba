"""ACT 疗法 Agent（Phase 3）。

基本流程（路由式）：
    五维评估 → 按 routing rules 进入核心过程 → 干预 → 步骤判断；
    完成 = recommended_processes 依次达成（通常以 committed_action 收尾）。
"""

from src.agents.therapy.base import FlowStep, TherapyAgentBase

_PROCESS_INDEX = {
    "defusion": 0,
    "acceptance": 1,
    "present_moment": 2,
    "self_as_context": 3,
    "values": 4,
    "committed_action": 5,
}


class ActTherapyAgent(TherapyAgentBase):
    therapy_name = "ACT"
    assessment_skill = "act_state_assessment"

    FLOW = [
        FlowStep("defusion", skills=["act_defusion"]),
        FlowStep("acceptance", skills=["act_acceptance"]),
        FlowStep("present_moment", skills=["act_present_moment"]),
        FlowStep("self_as_context", skills=["act_self_as_context"]),
        FlowStep("values", skills=["act_values"]),
        FlowStep("committed_action", skills=["act_committed_action"]),
    ]

    # ── 路由（规则来自评估 prompt 的 routing rules） ──────────────

    @staticmethod
    def _recommended_indices(assessment):
        out = []
        for rp in (assessment or {}).get("recommended_processes", []):
            p = rp.get("process")
            if p in _PROCESS_INDEX:
                out.append(_PROCESS_INDEX[p])
        return out

    def initial_step_index(self, assessment):
        a = assessment or {}
        fusion = a.get("fusion") or {}
        openness = a.get("openness") or {}
        avoidance = a.get("avoidance") or {}
        alignment = a.get("alignment") or {}
        escalation = a.get("escalation_level", "")
        if escalation in ("highly_escalated", "crisis_boundary") or openness.get("state") == "eo_shutdown":
            return _PROCESS_INDEX["present_moment"]
        if fusion.get("state") == "cf_identity_fusion":
            return _PROCESS_INDEX["self_as_context"]
        if fusion.get("index", 0) >= 0.65 and avoidance.get("index", 0) >= 0.65:
            return _PROCESS_INDEX["acceptance"]
        if alignment.get("index", 0.5) < 0.33:
            return _PROCESS_INDEX["values"]
        idxs = self._recommended_indices(a)
        return idxs[0] if idxs else 0

    def next_step_index(self, assessment, current, visited):
        """推荐过程依次达成（visited 为已达成过程名）；推荐列表走完 → 流程完成。"""
        for i in self._recommended_indices(assessment):
            if self.FLOW[i].name not in visited:
                return i
        return None

    def assessment_block(self, assessment, products):
        a = assessment or {}
        return {
            "fusion": a.get("fusion", {}),
            "avoidance": a.get("avoidance", {}),
            "openness": a.get("openness", {}),
            "alignment": a.get("alignment", {}),
            "activation": a.get("activation", {}),
        }
