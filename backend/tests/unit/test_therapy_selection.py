"""疗法家族构成的一致性测试。

权威定义处是各疗法 Agent 的 FLOW/BRANCHES/assessment_skill（决策线按此编排）；
本测试确保其引用的每个技能都已注册且类型正确——即"家族构成"这一
原 families.py 元数据的职责，由 Agent 单源 + 本测试守护。
"""

import pytest

from src.agents.therapy import (
    ActTherapyAgent,
    CbtTherapyAgent,
    DbtTherapyAgent,
    MiTherapyAgent,
    SfbtTherapyAgent,
)
from src.core.skill import SkillType
from src.core.state import THERAPIES, owner_for, therapy_key_for_owner

# Import the skills package so module-level registration runs.
# Mirrors production registration in bootstrap.py.
import src.skills  # noqa: F401 — registers all therapy skills
from src.core.skill import skill_registry

AGENTS = [CbtTherapyAgent, ActTherapyAgent, DbtTherapyAgent, MiTherapyAgent, SfbtTherapyAgent]


class TestOwnerProtocol:
    def test_all_five_therapies_roundtrip(self):
        for key in THERAPIES:
            assert therapy_key_for_owner(owner_for(key)) == key

    def test_daily_is_not_a_therapy_owner(self):
        assert therapy_key_for_owner("daily") is None


class TestTherapyAgentComposition:
    @pytest.mark.parametrize("cls", AGENTS, ids=lambda c: c.therapy_name)
    def test_therapy_name_is_known(self, cls):
        assert cls.therapy_name in THERAPIES

    @pytest.mark.parametrize("cls", AGENTS, ids=lambda c: c.therapy_name)
    def test_flow_skills_registered(self, cls):
        assert cls.FLOW, f"{cls.therapy_name}: 无主步骤"
        flow_names = set()
        for step in cls.FLOW:
            flow_names.update(step.skills)
        for branch in cls.BRANCHES.values():
            flow_names.add(branch.skill)
        assert flow_names, f"{cls.therapy_name}: 无流程技能"
        types = set()
        for name in flow_names:
            skill = skill_registry.get(name)
            assert skill is not None, f"{cls.therapy_name}: 技能 {name} 未注册"
            types.add(skill.skill_type)
        # 家族至少产出一个干预技能；流程中允许分析型步骤（如 CBT 识别/分类）
        assert SkillType.INTERVENTION in types, f"{cls.therapy_name}: 无 INTERVENTION 技能"
        assert types <= {SkillType.ANALYSIS, SkillType.INTERVENTION}, (
            f"{cls.therapy_name}: 流程技能类型异常: {types}"
        )

    @pytest.mark.parametrize("cls", AGENTS, ids=lambda c: c.therapy_name)
    def test_assessment_skill_registered_as_analysis(self, cls):
        if cls.assessment_skill is None:
            return  # CBT：分析由步骤 1/2 承担（见 cbt.py 注释）
        skill = skill_registry.get(cls.assessment_skill)
        assert skill is not None, f"{cls.therapy_name}: 评估技能未注册"
        assert skill.skill_type == SkillType.ANALYSIS

    @pytest.mark.parametrize("cls", AGENTS, ids=lambda c: c.therapy_name)
    def test_branch_return_targets_exist(self, cls):
        flow_names = {s.name for s in cls.FLOW}
        for branch in cls.BRANCHES.values():
            assert branch.return_to in flow_names, (
                f"{cls.therapy_name}: 分支 {branch.name} 的 return_to 不在主流程中"
            )
