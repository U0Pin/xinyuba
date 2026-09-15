"""SFBT 疗法 Agent（Phase 3）。

基本流程（需要式）：
    三维评估 → 资源意识低 → resource_exploration；problem_stuck → exception_exploration；
    目标模糊 → future_construction（先奇迹提问）；资源+目标具备 → small_step 落地；
    完成 = 愿景清晰 + 一小步落地。
"""

from src.agents.therapy.base import FlowStep, TherapyAgentBase

_SFBT_INDEX = {"resource_exploration": 0, "exception_exploration": 1, "future_construction": 2}


class SfbtTherapyAgent(TherapyAgentBase):
    therapy_name = "SFBT"
    assessment_skill = "sfbt_state_assessment"

    FLOW = [
        FlowStep("resource_exploration", skills=["sfbt_resource_exploration"]),
        FlowStep("exception_exploration", skills=["sfbt_exception_exploration"]),
        FlowStep("future_construction", skills=["sfbt_future_construction"]),
    ]

    def initial_step_index(self, assessment):
        a = assessment or {}
        ra = (a.get("resource_awareness") or {}).get("index", 0)
        po_state = (a.get("problem_orientation") or {}).get("state", "")
        gc = (a.get("goal_clarity") or {}).get("index", 0)
        if po_state == "problem_stuck":
            return _SFBT_INDEX["exception_exploration"]
        if ra < 0.4:
            return _SFBT_INDEX["resource_exploration"]
        if gc < 0.5:
            return _SFBT_INDEX["future_construction"]
        if ra >= 0.7 and gc >= 0.75:
            return _SFBT_INDEX["future_construction"]
        return 0

    def assessment_block(self, assessment, products):
        a = assessment or {}
        return {
            "resource_awareness": a.get("resource_awareness", {}),
            "problem_orientation": a.get("problem_orientation", {}),
            "goal_clarity": a.get("goal_clarity", {}),
        }
