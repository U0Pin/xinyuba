"""Tests for SFBT skills."""
import pytest
from src.skills.techniques import (
    SFBTProcess, SFBTResourceTechnique, SFBTExceptionTechnique, SFBTActionTechnique,
)
from src.skills.sfbt import (
    SfbtStateAssessment, _compute_sfbt_intervention_need,
)
from src.core.skill import SkillType, skill_registry


class TestSfbtTechniques:
    """Test SFBT technique enums are complete and valid."""

    def test_process_enum_has_three_values(self):
        assert len(SFBTProcess) == 3
        assert SFBTProcess.RESOURCE_EXPLORATION.value == "resource_exploration"
        assert SFBTProcess.EXCEPTION_EXPLORATION.value == "exception_exploration"
        assert SFBTProcess.FUTURE_CONSTRUCTION.value == "future_construction"

    def test_resource_techniques(self):
        assert SFBTResourceTechnique.COPING_QUESTIONS.value == "coping_questions"
        assert SFBTResourceTechnique.COMPLIMENTS_STRENGTHS.value == "compliments_strengths"

    def test_exception_techniques(self):
        assert SFBTExceptionTechnique.EXCEPTION_FINDING.value == "exception_finding"
        assert SFBTExceptionTechnique.SCALING_QUESTIONS.value == "scaling_questions"

    def test_action_techniques(self):
        assert SFBTActionTechnique.MIRACLE_QUESTION.value == "miracle_question"
        assert SFBTActionTechnique.SMALL_STEP.value == "small_step"


class TestSfbtStateAssessment:
    """Test sfbt_state_assessment skill."""

    def test_registered(self):
        skill = skill_registry.get("sfbt_state_assessment")
        assert skill is not None
        assert skill.skill_type == SkillType.ANALYSIS

    def test_no_llm_returns_error(self):
        skill = SfbtStateAssessment()
        result = skill.execute({"user_text": "hello"}, {})
        assert result.success is False
        assert "error" in result.output


class TestSfbtInterventionFormula:
    """Test backend formula for SFBT intervention need."""

    def test_problem_stuck_drives_priority(self):
        # Normal problem expression (not stuck)
        priority_normal = _compute_sfbt_intervention_need(
            goal_clarity=0.5, resource_awareness=0.5, problem_stuck_value=0.0
        )
        # Stuck user
        priority_stuck = _compute_sfbt_intervention_need(
            goal_clarity=0.5, resource_awareness=0.5, problem_stuck_value=0.8
        )
        assert priority_stuck > priority_normal
        assert priority_stuck > 0.25  # 0.35 * 0.8 = 0.28

    def test_only_problem_stuck_is_penalized(self):
        # problem_stuck_value=0.0 → no problem penalty even if other vars are low
        priority = _compute_sfbt_intervention_need(
            goal_clarity=0.5, resource_awareness=0.5, problem_stuck_value=0.0
        )
        # Base penalty from unclear goal + low resources: 0.35*0.5 + 0.30*0.5 = 0.325
        assert 0.30 < priority < 0.35

    def test_clear_goals_lowers_priority(self):
        priority_vague = _compute_sfbt_intervention_need(
            goal_clarity=0.1, resource_awareness=0.5, problem_stuck_value=0.0
        )
        priority_clear = _compute_sfbt_intervention_need(
            goal_clarity=0.9, resource_awareness=0.5, problem_stuck_value=0.0
        )
        assert priority_vague > priority_clear


class TestSfbtInterventions:
    """Test SFBT intervention skills are correctly registered."""

    @pytest.mark.parametrize("name,expected_type", [
        ("sfbt_resource_exploration", SkillType.INTERVENTION),
        ("sfbt_exception_exploration", SkillType.INTERVENTION),
        ("sfbt_future_construction", SkillType.INTERVENTION),
    ])
    def test_skill_registered_and_type(self, name, expected_type):
        skill = skill_registry.get(name)
        assert skill is not None, f"Skill {name} not found"
        assert skill.skill_type == expected_type

    @pytest.mark.parametrize("skill_name", [
        "sfbt_resource_exploration", "sfbt_exception_exploration", "sfbt_future_construction",
    ])
    def test_fallback_always_succeeds(self, skill_name):
        skill = skill_registry.get(skill_name)
        result = skill.fallback({"assessment": {"resource_awareness": {"state": "low"}}})
        assert result.success is True
        assert result.output["technique"]["name"] is not None

    @pytest.mark.parametrize("skill_name", [
        "sfbt_resource_exploration", "sfbt_exception_exploration", "sfbt_future_construction",
    ])
    def test_adaptation_fields_are_correct(self, skill_name):
        skill = skill_registry.get(skill_name)
        result = skill.fallback({"assessment": {"resource_awareness": {"state": "low"}}})
        adaptation = result.output["adaptation"]
        assert adaptation["tone_adjustment"] == "hopeful"
        assert adaptation["pace_adjustment"] == "future_oriented"
