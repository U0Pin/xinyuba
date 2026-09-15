"""DBT 疗法 Agent（Phase 3）。

基本流程（优先级式）：
    五维评估（后端公式定优先级）→ 模块按优先级推进（低 mindfulness 先打底；
    痛苦/冲动高 → distress_tolerance 先于 emotion_regulation；冲突高 → interpersonal）
    → 干预 → 步骤判断；完成 = 推荐模块依次达成。
"""

from src.agents.therapy.base import FlowStep, TherapyAgentBase

_MODULE_INDEX = {
    "mindfulness": 0,
    "distress_tolerance": 1,
    "emotion_regulation": 2,
    "interpersonal_effectiveness": 3,
}


class DbtTherapyAgent(TherapyAgentBase):
    therapy_name = "DBT"
    assessment_skill = "dbt_state_assessment"

    FLOW = [
        FlowStep("mindfulness", skills=["dbt_mindfulness"]),
        FlowStep("distress_tolerance", skills=["dbt_distress_tolerance"]),
        FlowStep("emotion_regulation", skills=["dbt_emotion_regulation"]),
        FlowStep("interpersonal_effectiveness", skills=["dbt_interpersonal_effectiveness"]),
    ]

    @staticmethod
    def _recommended_indices(assessment):
        out = []
        for rp in (assessment or {}).get("recommended_processes", []):
            p = rp.get("process")
            if p in _MODULE_INDEX:
                out.append(_MODULE_INDEX[p])
        return out

    def initial_step_index(self, assessment):
        idxs = self._recommended_indices(assessment)
        return idxs[0] if idxs else 0

    def next_step_index(self, assessment, current, visited):
        """推荐模块依次达成（visited 为已达成模块名）；推荐列表走完 → 流程完成。"""
        for i in self._recommended_indices(assessment):
            if self.FLOW[i].name not in visited:
                return i
        return None

    def assessment_block(self, assessment, products):
        a = assessment or {}
        return {
            "mindfulness_awareness": a.get("mindfulness_awareness", {}),
            "emotion_dysregulation": a.get("emotion_dysregulation", {}),
            "distress_level": a.get("distress_level", {}),
            "impulsivity": a.get("impulsivity", {}),
            "interpersonal_conflict": a.get("interpersonal_conflict", {}),
        }
